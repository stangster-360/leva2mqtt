"""Wires the link, the detection logic and the outputs together."""
from __future__ import annotations

import asyncio
import logging
import signal
import time
from typing import Any, Optional

from . import telegram, visualizer
from .config import Config
from .display import Display
from .frame import Frame
from .link import Link
from .machine import (BREWING, HOT_WATER, MODE_AWAKE, MODE_ECO, OFFLINE, STEAM, Settings,
                      Tracker, WakeReporter)
from .modes import ModeController
from .outbox import Entry, Outbox
from .shots import SHOT, Run, ShotRecorder

log = logging.getLogger(__name__)

HEALTH_FILE = "/tmp/leva2mqtt.health"
COMMANDS = {"wake": "MCa", "eco": "MCe", "standby": "MCE", "report": "MCcREPORT SP1"}


class Bridge:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        b = cfg.bridge
        self.tracker = Tracker(Settings(at_temp_band=b.ready_band, ready_spread=b.ready_spread,
                                        ready_grace=b.ready_grace,
                                        steam_on=b.steam_threshold,
                                        steam_off=b.steam_threshold - 1.0))
        self.reporter = WakeReporter(b.report_min_offline)
        self.recorder = ShotRecorder(tail=cfg.shots.tail)
        self.settings: dict[str, Any] = {
            "dose": cfg.shots.dose, "bean": "", "grinder_setting": ""}
        if cfg.visualizer_enabled:
            self.settings["visualizer_upload"] = cfg.visualizer.upload
        if cfg.telegram_enabled:
            self.settings["telegram_notify"] = cfg.telegram.notify
        self.modes = ModeController(interval=b.alert_interval, quiet=b.other_client_quiet)
        self.link = Link(cfg.machine.host, cfg.machine.port, self._on_frame, self._on_link,
                         on_display=self._on_display, on_foreign=self._on_foreign)
        self.mqtt = None
        self._last_publish = 0.0
        self._last_state: Optional[str] = None
        self._tasks: set[asyncio.Task] = set()
        self._last_shot: Optional[dict] = None
        self._last_live: Optional[dict] = None
        self.outbox = Outbox(cfg.shots.directory, cfg.shots.keep, self._senders(),
                             on_delivered=self._on_delivered, on_change=self._on_queue)

    # --- main ------------------------------------------------------------

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        if self.cfg.mqtt_enabled:
            from .mqtt import MqttOutput
            self.mqtt = MqttOutput(self.cfg, loop, self._on_command, self._on_setting,
                                   self.settings)
            self.mqtt.start()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self.link.stop)
            except NotImplementedError:   # Windows
                pass
        health = self._spawn(self._health())
        ticker = self._spawn(self._ticker())
        self.outbox.load()
        outbox = loop.create_task(self.outbox.run())
        log.info("leva2mqtt started: machine %s:%s, outputs: %s", self.cfg.machine.host,
                 self.cfg.machine.port, ", ".join(self._outputs()) or "none")
        try:
            await self.link.run()
        finally:
            health.cancel()
            ticker.cancel()
            rest = self._tasks - {health, ticker}
            if rest:
                await asyncio.wait(rest, timeout=10)
            # let a running upload finish; unfinished shots stay in pending/
            self.outbox.stop()
            try:
                await asyncio.wait_for(outbox, 20)
            except asyncio.TimeoutError:
                log.warning("delivery still running at shutdown, retried on next start")
            if self.mqtt:
                self.mqtt.stop()
            log.info("stopped")

    def _outputs(self) -> list[str]:
        out = []
        if self.cfg.mqtt_enabled:
            out.append("mqtt")
        if self.cfg.visualizer_enabled:
            out.append("visualizer")
        if self.cfg.telegram_enabled:
            out.append("telegram")
        if self.cfg.shots.directory:
            out.append(f"shot files ({self.cfg.shots.directory}, "
                       f"{'keep' if self.cfg.shots.keep else 'delete'} after upload)")
        return out

    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def _health(self) -> None:
        """Health = connected to the broker (or MQTT not used). Not the machine:
        a machine that is switched off is normal."""
        while True:
            if self.mqtt is None or self.mqtt.connected:
                try:
                    with open(HEALTH_FILE, "w") as fh:
                        fh.write(str(time.time()))
                except OSError:
                    pass
            await asyncio.sleep(10)

    async def _ticker(self) -> None:
        """Drives the alert poll and the end of an other-client phase."""
        while True:
            await asyncio.sleep(0.5)
            if self.link.up:
                self._mode_request(self.modes.tick(time.time(), self._idle()))

    def _idle(self) -> bool:
        return (self.tracker.mode in (MODE_AWAKE, MODE_ECO)
                and self.tracker.state not in (BREWING, STEAM, HOT_WATER)
                and self.recorder.run is None)

    def _mode_request(self, mode: Optional[str]) -> None:
        self.link.hold = self.modes.other_client
        if mode:
            self._spawn(self.link.request(mode))
        if self.modes.take_changed() and self.mqtt and self._last_live:
            self.mqtt.publish_live(self._last_live | self._mode_values())

    # --- link events -----------------------------------------------------

    def _on_link(self, up: bool, t: float, offline_since: Optional[float]) -> None:
        if up:
            log.info("machine reachable" + (f" after {t - offline_since:.0f} s offline"
                                            if offline_since else ""))
            self.reporter.link_up(t, offline_since)
            self.modes.link_up(t)
            if self.mqtt:
                self.mqtt.publish_machine(True)
            return
        log.info("machine lost")
        run = self.recorder.abort()
        if run:
            self._finished(run)
        self.tracker.link_down()
        self.modes.link_down()
        self.link.hold = False
        self._last_live = None
        if self.mqtt:
            self.mqtt.publish_machine(False)
            self.mqtt.publish_live({"state": OFFLINE})
        self._last_state = OFFLINE

    def _on_display(self, d: Display, t: float) -> None:
        self._mode_request(self.modes.display(d, t))
        if self.modes.other_client and d.main:
            # another client holds the module in display mode: use what the display
            # shows (no shot logging, no per-minute average)
            self._process(_frame_from_display(d), t, record=False)

    def _on_foreign(self, reason: str, t: float) -> None:
        self.modes.foreign(reason, t)
        self._mode_request(None)

    def _on_frame(self, f: Frame, t: float) -> None:
        self._process(f, t)

    def _process(self, f: Frame, t: float, record: bool = True) -> None:
        change = self.tracker.update(f, t)
        if change:
            log.info("power mode %s -> %s", change[0] or "unknown", change[1])
            self.reporter.mode_change(change[0], change[1], t)
        if self.reporter.due(self.tracker.mode):
            self._spawn(self._send_report())
        if record:
            run = self.recorder.update(f, t)
            if run:
                self._finished(run)
        state = self.tracker.state
        if state != self._last_state:
            log.info("state %s", state)
        self._last_live = self._live(f)
        if self.mqtt and (state != self._last_state or state == BREWING
                          or self.modes.take_changed()
                          or t - self._last_publish >= self.cfg.mqtt.publish_interval):
            self._last_publish = t
            self.mqtt.publish_live(self._last_live | self._mode_values())
        self._last_state = state

    def _live(self, f: Frame) -> dict:
        tr = self.tracker
        values = {
            "state": tr.state, "mode": f.mode,
            "pump": bool(f.pump), "heating": tr.heating, "ready": tr.ready,
            "at_temperature": tr.at_temperature,
            "steam": tr.steam, "hot_water": tr.hot_water,
        }
        for key in ("temperature", "setpoint", "heater", "heater_avg", "pressure",
                    "target_pressure", "weight", "flow_weight", "flow_rate", "volume",
                    "profile", "pid_p", "pid_i", "pid_d"):
            values[key] = getattr(f, key)
        return values

    def _mode_values(self) -> dict:
        alerts = self.modes.alerts
        return {"alerts": None if alerts is None else list(alerts),
                "alert": None if alerts is None else (", ".join(alerts) or "none"),
                "problem": bool(alerts), "other_client": self.modes.other_client}

    async def _send_report(self) -> None:
        # Requirement: MCcREPORT SP1, then MCr. While another client holds the module
        # in display mode the MCr is left out, so that client keeps working.
        self.modes.cancel_poll()
        if await self.link.send("MCcREPORT SP1"):
            await asyncio.sleep(0.5)
            if not self.modes.other_client:
                await self.link.request("mcr")

    # --- Home Assistant input -------------------------------------------

    def _on_command(self, name: str) -> None:
        cmd = COMMANDS.get(name)
        if cmd is None:
            return
        log.info("button %s", name)
        if name == "report":
            self.reporter.manual_report(time.time())
            self._spawn(self._send_report())
        else:
            self._spawn(self.link.send(cmd))

    def _on_setting(self, key: str, payload: str) -> None:
        from .mqtt import parse_setting
        if key not in self.settings:
            return
        value = parse_setting(key, payload)
        if value is None:
            log.warning("ignoring invalid value for %s: %r", key, payload)
        elif value != self.settings[key]:
            self.settings[key] = value
            log.info("setting %s = %r", key, value)
        if self.mqtt:
            self.mqtt.publish_setting(key, self.settings[key])

    # --- finished runs ---------------------------------------------------

    def _record(self, run: Run) -> dict:
        rec = run.summary()
        dose = self.settings.get("dose") if run.kind == SHOT else None
        if dose:
            rec["dose"] = dose
            if run.yield_g:
                rec["ratio"] = round(run.yield_g / dose, 2)
        if run.kind == SHOT:
            for key in ("bean", "grinder_setting"):
                if self.settings.get(key):
                    rec[key] = self.settings[key]
        return rec

    def _finished(self, run: Run) -> None:
        rec = self._record(run)
        if self.mqtt:
            self.mqtt.publish_run(rec)
        if run.kind != SHOT:
            return
        self._last_shot = rec
        shot = visualizer.to_decent_json(run, rec.get("dose"), rec.get("bean"),
                                         rec.get("grinder_setting"))
        targets = []
        if self.cfg.visualizer_enabled and self.settings.get("visualizer_upload"):
            targets.append("visualizer")
        if self.cfg.telegram_enabled and self.settings.get("telegram_notify"):
            targets.append("telegram")
        name = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(run.start)) + "_shot.json"
        self.outbox.add(name, shot, rec, targets)

    # --- outbox targets --------------------------------------------------

    def _senders(self) -> dict:
        senders = {}
        if self.cfg.visualizer_enabled:
            senders["visualizer"] = self._send_visualizer
        if self.cfg.telegram_enabled:
            senders["telegram"] = self._send_telegram
        return senders

    async def _send_visualizer(self, e: Entry) -> dict:
        url = await asyncio.to_thread(visualizer.upload, e.shot, self.cfg.visualizer.username,
                                      self.cfg.visualizer.password)
        return {"url": url}

    async def _send_telegram(self, e: Entry) -> dict:
        url = e.delivery.get("visualizer", {}).get("url")
        text = telegram.format_shot(e.record, self.cfg.machine.name, url)
        await asyncio.to_thread(telegram.send, self.cfg.telegram.token,
                                self.cfg.telegram.chat_id, text)
        return {}

    def _on_delivered(self, e: Entry, target: str, info: dict) -> None:
        if target != "visualizer" or not self.mqtt:
            return
        url = info.get("url")
        self.mqtt.publish_event({"event": "shot_uploaded", "start": e.record.get("start"),
                                 "visualizer_url": url})
        # only update last_shot if no newer shot has replaced it meanwhile
        if self._last_shot and self._last_shot.get("start") == e.record.get("start"):
            self._last_shot["visualizer_url"] = url
            self.mqtt.update_record(self._last_shot)

    def _on_queue(self, count: int) -> None:
        if self.mqtt:
            self.mqtt.publish_pending(count)


def _frame_from_display(d: Display) -> Frame:
    """The values a main screen shows, as a Frame (all other fields unknown)."""
    return Frame(
        raw=d.raw, pump=d.shot, mode=None if d.standby else ("ECO" if d.eco else "PID"),
        temperature=d.temperature, setpoint=d.setpoint, heater=d.heater,
        pid_p=None, pid_i=None, pid_d=None, temperature_rate=None, heater_avg=None,
        volume=None,   # the display shows the last shot's volume, not the live counter
        pulses=None, flow_rate=None, target_pressure=None, pressure=d.pressure,
        weight=d.weight, flow_weight=None, profile=d.profile)
