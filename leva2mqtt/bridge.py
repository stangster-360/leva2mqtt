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
from .frame import COUNTERS, Frame
from .link import Link
from .machine import (BREWING, HOT_WATER, MODE_AWAKE, MODE_ECO, OFFLINE, STEAM, Settings,
                      Tracker, WakeReporter)
from .modes import ModeController
from .outbox import DONE, PENDING, Entry, Outbox, PermanentError
from .shots import SHOT, Run, ShotRecorder

log = logging.getLogger(__name__)

# frame fields published as live values (entities appear once a value is seen)
PID2_KEYS = ("mode2", "temperature2", "setpoint2", "heater2", "heater2_avg", "pid2_p", "pid2_i",
             "pid2_d", "pid2_ff", "pid2_kc", "pid2_ti", "pid2_td", "pid2_windup",
             "temperature2_rate")
FRAME_KEYS = ("temperature", "setpoint", "heater", "heater_avg", "pressure", "target_pressure",
              "weight", "flow_weight", "flow_rate", "volume", "profile", "pid_p", "pid_i",
              "pid_d", "pid_kc", "pid_ti", "pid_td", "pump_phase", "pump_phase_bias",
              "pump_phase_p", "pump_phase_i", "pressure_min", "pressure_max", "pid_ff",
              "pid_windup", "temperature_rate", "temperature_pcb", "temperature_aux1",
              "temperature_aux2", "temperature_aux3", "flow_target") + PID2_KEYS

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
        if cfg.shots.grinders:
            # one grinder: preselected; several: chosen in Home Assistant
            self.settings["grinder"] = cfg.shots.grinders[0] if len(cfg.shots.grinders) == 1 else ""
        if cfg.visualizer_enabled:
            self.settings["visualizer_upload"] = cfg.visualizer.upload
            self.settings["coffee_bag"] = ""
        self._bags: Optional[dict[str, str]] = None     # label -> id, None = not loaded
        if cfg.telegram_enabled:
            self.settings["telegram_notify"] = cfg.telegram.notify
        self.modes = ModeController(interval=b.alert_interval, quiet=b.other_client_quiet)
        self.link = Link(cfg.machine.host, cfg.machine.port, self._on_frame, self._on_link,
                         on_display=self._on_display, on_foreign=self._on_foreign)
        self.link.on_slots = self._on_slots
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
        bags = None
        if self.cfg.visualizer_enabled and self.cfg.visualizer.coffee_bags_refresh > 0:
            bags = self._spawn(self._bag_loop())
        self.outbox.load()
        outbox = loop.create_task(self.outbox.run())
        log.info("leva2mqtt started: machine %s:%s, outputs: %s", self.cfg.machine.host,
                 self.cfg.machine.port, ", ".join(self._outputs()) or "none")
        try:
            await self.link.run()
        finally:
            health.cancel()
            ticker.cancel()
            if bags:
                bags.cancel()
            rest = self._tasks - {health, ticker, bags}
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
        if self.cfg.shots.grinders:
            out.append("grinders " + ", ".join(self.cfg.shots.grinders))
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

    def _on_slots(self, full: bool) -> None:
        if self.mqtt:
            self.mqtt.publish_slots(full)

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
        self._last_live = self._live(f, t)
        if self.mqtt and (state != self._last_state or state == BREWING
                          or self.modes.take_changed()
                          or t - self._last_publish >= self.cfg.mqtt.publish_interval):
            self._last_publish = t
            self.mqtt.publish_live(self._last_live | self._mode_values())
        self._last_state = state

    def _live(self, f: Frame, t: float) -> dict:
        tr = self.tracker
        pump_time = tr.pump_time(t)
        values = {
            "state": tr.state, "mode": f.mode,
            "pump": bool(f.pump), "heating": tr.heating, "ready": tr.ready,
            "at_temperature": tr.at_temperature,
            "steam": tr.steam, "hot_water": tr.hot_water,
            "pump_time": None if pump_time is None else round(pump_time, 1),
            "heater_avg_spread": tr.heater_avg_spread,
            "heater_avgs": list(tr.heater_avgs),
        }
        for key in FRAME_KEYS:
            values[key] = getattr(f, key)
        if f.mode2 in (None, "OFF"):
            # PID 2 not in use (the usual case on a single boiler): no entities for it
            for key in PID2_KEYS:
                values[key] = None
        slots = [f.profile1, f.profile2, f.profile3, f.profile4]
        values["profiles"] = [p for p in slots if p] or None
        values["bad_frames"] = COUNTERS["bad_checksum"]
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
        if name == "refresh_coffee_bags":
            # not a machine command, so it must be handled before the COMMANDS lookup
            log.info("button %s", name)
            self._spawn(self._load_bags(manual=True))
            return
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
        if key == "coffee_bag" and value and self._bags is not None and value not in self._bags:
            value = None   # unknown bag; a restored value before the first load is kept
        if key == "grinder" and value and value not in self.cfg.shots.grinders:
            value = None   # e.g. removed from the configuration
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
            # with coffee bags the "Bean" entity is hidden; a leftover value is not used
            keys = ("grinder", "grinder_setting") if self._bags else ("bean", "grinder",
                                                                      "grinder_setting")
            for key in keys:
                if self.settings.get(key):
                    rec[key] = self.settings[key]
            label = self.settings.get("coffee_bag")
            if label and self._bags and label in self._bags:
                rec["coffee_bag"] = label
                rec["coffee_bag_id"] = self._bags[label]
        return rec

    def _finished(self, run: Run) -> None:
        rec = self._record(run)
        if self.mqtt:
            self.mqtt.publish_run(rec)
        if run.kind != SHOT:
            return
        self._last_shot = rec
        shot = visualizer.to_decent_json(run, rec.get("dose"), rec.get("bean"),
                                         rec.get("grinder_setting"), rec.get("grinder"))
        targets = []
        if self.cfg.visualizer_enabled and self.settings.get("visualizer_upload"):
            targets.append("visualizer")
            if rec.get("coffee_bag_id"):
                targets.append("coffee_bag")
        if self.cfg.telegram_enabled and self.settings.get("telegram_notify"):
            targets.append("telegram")
        name = time.strftime("%Y-%m-%d_%H%M%S", time.localtime(run.start)) + "_shot.json"
        self.outbox.add(name, shot, rec, targets, visualizer.extra_series(run))

    # --- outbox targets --------------------------------------------------

    def _senders(self) -> dict:
        senders = {}
        if self.cfg.visualizer_enabled:
            senders["visualizer"] = self._send_visualizer
            senders["coffee_bag"] = self._send_coffee_bag
        if self.cfg.telegram_enabled:
            senders["telegram"] = self._send_telegram
        return senders

    async def _send_visualizer(self, e: Entry) -> dict:
        return await asyncio.to_thread(visualizer.upload, e.shot, self.cfg.visualizer.username,
                                       self.cfg.visualizer.password)

    async def _send_coffee_bag(self, e: Entry) -> dict:
        up = e.delivery.get("visualizer", {})
        if up.get("state") == PENDING:
            raise RuntimeError("waiting for the upload")
        if up.get("state") != DONE or not up.get("id"):
            raise PermanentError("shot was not uploaded")
        await asyncio.to_thread(visualizer.link_coffee_bag, up["id"], e.record["coffee_bag_id"],
                                self.cfg.visualizer.username, self.cfg.visualizer.password)
        return {}

    # --- coffee bags -----------------------------------------------------

    async def _bag_loop(self) -> None:
        while True:
            await self._load_bags()
            await asyncio.sleep(self.cfg.visualizer.coffee_bags_refresh)

    async def _load_bags(self, manual: bool = False) -> None:
        """Reload the active coffee bags. On an error the last list stays; an empty list
        (no bags, coffee management not used) removes the select entity."""
        try:
            bags = await asyncio.to_thread(visualizer.coffee_bags, self.cfg.visualizer.username,
                                           self.cfg.visualizer.password)
        except Exception as err:
            log.warning("cannot load coffee bags from Visualizer: %s", err)
            return
        self.apply_bags(bags)
        if manual:
            log.info("coffee bags reloaded: %d active", len(self._bags or {}))

    def apply_bags(self, bags: list[dict]) -> None:
        old = self._bags
        self._bags = {b["label"]: b["id"] for b in bags}
        if old is None or set(old) != set(self._bags):
            log.info("%d active coffee bag(s) on Visualizer: %s", len(self._bags),
                     "; ".join(self._bags) or "none")
        selected = self.settings.get("coffee_bag")
        if selected and selected not in self._bags:
            log.warning("coffee bag %r is not active any more, selection cleared", selected)
            self.settings["coffee_bag"] = ""
            if self.mqtt:
                self.mqtt.publish_setting("coffee_bag", "")
        if self.mqtt:
            self.mqtt.set_coffee_bags(list(self._bags) or None)

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
