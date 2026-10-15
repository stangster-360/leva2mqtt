#!/usr/bin/env python3
"""
monitor.py – live view of the ito / leva! telnet stream, decoded and formatted.

    python3 tools/monitor.py <ito-ip> [--mode mcr|display|none] [--interval 1] [--lines]
                             [--raw] [--no-keepalive] [--no-resend] [--wake] [--port 23]

Connects like capture.py, but writes no file: it decodes the stream and shows the
fields known so far (docs/protocol.md) every --interval seconds.

- Screen view (default in a terminal): one page, redrawn in place. Sensors the frame
  does not deliver ('?', e.g. no scale, standby) are left out.
- --lines: one compact line per interval, for scrolling or piping into a file
  (also used automatically when the output is not a terminal).
- --raw: also show the last raw frame with character positions, to spot fields
  that are not decoded yet.

Connection behaviour (same rules as capture.py and the bridge):
- sends the start command of --mode on every connect (mcr = MCr, display = MC@,
  none = nothing), a bare newline every 5 s (--no-keepalive turns it off), and
  re-sends the start command while no frame of the wanted kind has arrived for 5 s
  (heartbeat-only state after power-on, or another client switched the global report
  mode). --no-resend turns that off, e.g. to watch while the Leva Companion is open.
- reconnects every 2 s when the module is unreachable, and drops a connection on
  which nothing at all arrived for 3 s (the module does not close it on power loss).
- --wake sends MCcREPORT SP1 once after the first connect: this wakes the machine
  from standby / eco and it beeps. Without --wake the tool never wakes the machine.

Notes such as "unverified" next to a value mean what they say in docs/protocol.md:
the field or its meaning is not confirmed by a capture yet. "state" uses the bridge's
own detection rules (leva2mqtt/machine.py), whose thresholds are unverified. The
"Ready" section shows the bridge's ready decision step by step: boiler within ±0.5 K and
the last three per-minute heater averages (pos 73-77) within less than 1.0 percentage
point, decided at each update of that field (docs/detection-rules.md, section 3).

Stop with Ctrl+C. Python 3.8+, standard library only; run it from the repository
(it uses the bridge's frame parser).
"""
from __future__ import annotations

import argparse
import os
import shutil
import socket
import sys
import time
from collections import deque
from datetime import datetime
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    from leva2mqtt.frame import Frame, parse  # noqa: E402
    from leva2mqtt.display import parse_display  # noqa: E402
    from leva2mqtt.machine import MODE_AWAKE, Tracker  # noqa: E402
except ModuleNotFoundError as e:   # tools/ used without the package next to it
    sys.exit(f"{e}: run this from a checkout that has leva2mqtt/ next to tools/ "
             "(sparse checkout: git sparse-checkout set tools leva2mqtt)")

PORT = 23
CONNECT_TIMEOUT_S = 3.0
RETRY_S = 2.0
KEEPALIVE_S = 5.0
RESEND_S = 5.0
WATCHDOG_S = 3.0
STREAM_WINDOW_S = 2.0   # what arrived in this window decides the "stream" status
DISPLAY_SHOW_S = 10.0   # keep the display section this long after the last display frame

START = {"mcr": ["MCr"], "display": ["MC@"], "none": []}
WANTED = {"mcr": "{", "display": "<"}

INPUT_EVENTS = {"0": None, "1": "encoder turned", "2": "encoder turned (other way)", "3": "click"}
SCREENS = {"1": "menu", "2": "main / shot screen"}


def clock(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M:%S")


def duration(s: float) -> str:
    s = int(s)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    return f"{s // 3600}h{s // 60 % 60:02d}m"


def num(v, fmt: str, unit: str = "") -> Optional[str]:
    if v is None:
        return None
    return f"{v:{fmt}}" + (f" {unit}" if unit else "")


def joined(parts: List[Tuple[str, object, str]], unit: str = "") -> Optional[str]:
    """'P 1.1 · I 7.1' from (label, value, fmt) triples; missing values are left out."""
    out = [f"{label} {value:{fmt}}" for label, value, fmt in parts if value is not None]
    if not out:
        return None
    return " · ".join(out) + (f" {unit}" if unit else "")


class Display:
    """A display frame decoded by the bridge's parser (leva2mqtt/display.py), plus a
    plain-text rendering of the four LCD lines and a description of the code."""

    def __init__(self, d):
        self.code = d.code
        self.text = "  ".join(x.strip() for x in d.lines if x.strip()).replace("\xdf", "°")
        self.alerts = list(d.alerts)
        self.ready = d.ready

    def describe(self) -> str:
        on, event, screen = self.code
        parts = [SCREENS.get(screen, f"screen {screen} (unknown)"),
                 "display on" if on == "1" else "display off" if on == "0" else f"display {on}?"]
        if event != "0":
            parts.append(INPUT_EVENTS.get(event) or f"input {event} (unknown)")
        return f"{self.code}  " + " · ".join(parts)


class Monitor:
    def __init__(self, host: str, port: int, mode: str, keepalive: bool, resend: bool,
                 wake: bool):
        self.host, self.port, self.mode = host, port, mode
        self.use_keepalive, self.use_resend, self.wake = keepalive, resend, wake
        self.sock: Optional[socket.socket] = None
        self.tracker = Tracker()
        self.events: deque = deque(maxlen=6)
        self.new_events: List[str] = []
        self.connected_since: Optional[float] = None
        self.offline_since: Optional[float] = None
        self.last_rx = 0.0
        self.last_wanted = 0.0
        self.last_start_sent = 0.0
        self.last_keepalive = 0.0
        self.resends = 0
        # latest data
        self.frame: Optional[Frame] = None
        self.frame_t = 0.0
        self.frame_times: deque = deque()
        self.display: Optional[Display] = None
        self.display_t = 0.0
        self.last_ready_t: Optional[float] = None
        self.last_alert: Optional[Tuple[str, float]] = None
        self.heartbeat_t = 0.0
        self.other_t = 0.0
        self.other_text = ""
        self.pump_since: Optional[float] = None
        self.ready_since: Optional[float] = None
        self.last_pump: Optional[Tuple[float, float]] = None   # (duration, end time)
        self.lines_rx = 0

    # --- connection -------------------------------------------------------------

    def event(self, text: str) -> None:
        t = time.time()
        self.events.append((t, text))
        self.new_events.append(f"{clock(t)} # {text}")

    def send(self, cmd: str, quiet: bool = False) -> None:
        if not self.sock:
            return
        try:
            self.sock.sendall(cmd.encode("latin-1") + b"\r\n")
            if not quiet:
                self.event(f">> {cmd}")
        except OSError:
            pass

    def connect(self) -> bool:
        try:
            s = socket.create_connection((self.host, self.port), timeout=CONNECT_TIMEOUT_S)
        except OSError as e:
            if self.offline_since is None:
                self.offline_since = time.time()
                self.event(f"offline ({e.__class__.__name__}), retrying every {RETRY_S:.0f} s")
            return False
        s.settimeout(0.2)
        self.sock = s
        now = time.time()
        if self.offline_since is not None:
            self.event(f"connected again after {duration(now - self.offline_since)} offline")
        else:
            self.event(f"connected to {self.host}:{self.port}")
        self.offline_since = None
        self.connected_since = self.last_rx = self.last_wanted = self.last_keepalive = now
        if self.wake:
            self.wake = False
            self.send("MCcREPORT SP1")
            time.sleep(0.3)
        for cmd in START[self.mode]:
            self.send(cmd)
            time.sleep(0.3)
        self.last_start_sent = time.time()
        return True

    def disconnect(self, reason: str) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
        self.sock = None
        self.connected_since = None
        self.offline_since = time.time()
        self.tracker.link_down()
        self.pump_since = self.ready_since = None
        self.event(f"connection lost ({reason})")

    def timers(self, now: float) -> None:
        if not self.sock:
            return
        if now - self.last_rx > WATCHDOG_S:
            self.disconnect(f"nothing received for {WATCHDOG_S:.0f} s")
            return
        if self.use_keepalive and now - self.last_keepalive >= KEEPALIVE_S:
            self.last_keepalive = now
            try:
                self.sock.sendall(b"\n")
            except OSError:
                pass
        if (self.use_resend and START[self.mode] and now - self.last_wanted > RESEND_S
                and now - self.last_start_sent > RESEND_S):
            self.resends += 1
            for cmd in START[self.mode]:
                self.send(cmd)
            self.last_start_sent = now

    # --- decoding ---------------------------------------------------------------

    def handle(self, text: str, t: float) -> None:
        self.lines_rx += 1
        if text[0] == WANTED.get(self.mode):
            self.last_wanted = t
        if text[0] == "{":
            f = parse(text)
            if f is None:
                return
            self.frame, self.frame_t = f, t
            self.frame_times.append(t)
            self.tracker.update(f, t)
            if self.tracker.ready and self.ready_since is None:
                self.ready_since = t
            elif not self.tracker.ready:
                self.ready_since = None
            if f.pump and self.pump_since is None:
                self.pump_since = t
            elif not f.pump and self.pump_since is not None:
                self.last_pump = (t - self.pump_since, t)
                self.pump_since = None
        elif text[0] == "<":
            parsed = parse_display(text)
            if parsed is None:
                return
            d = Display(parsed)
            self.display, self.display_t = d, t
            if d.ready:
                self.last_ready_t = t
            if d.alerts:
                self.last_alert = (", ".join(d.alerts), t)
        elif text.startswith("$id"):
            self.heartbeat_t = t
        else:
            self.other_t, self.other_text = t, text

    def run(self, interval: float, render) -> None:
        buf = b""
        next_render = 0.0
        while True:
            now = time.time()
            if now >= next_render:
                render(self, now)
                next_render = now + interval
            if not self.sock:
                if not self.connect():
                    time.sleep(min(RETRY_S, max(0.05, next_render - time.time())))
                    continue
                buf = b""
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                chunk = None
            except OSError as e:
                self.disconnect(str(e) or e.__class__.__name__)
                continue
            if chunk == b"":
                self.disconnect("closed by module")
                continue
            now = time.time()
            if chunk:
                self.last_rx = now
                buf += chunk
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    text = raw.rstrip(b"\r").decode("latin-1")
                    if text:
                        self.handle(text, now)
            self.timers(now)

    # --- derived status ---------------------------------------------------------

    def frame_rate(self, now: float) -> float:
        while self.frame_times and now - self.frame_times[0] > 5:
            self.frame_times.popleft()
        span = min(5.0, now - self.connected_since) if self.connected_since else 5.0
        return len(self.frame_times) / span if span > 0.5 else 0.0

    def stream(self, now: float) -> str:
        if not self.sock:
            return "offline" if self.offline_since is not None else "connecting"
        recent = lambda t: now - t <= STREAM_WINDOW_S  # noqa: E731
        if recent(self.frame_t):
            return f"MCr {self.frame_rate(now):.1f} frames/s"
        if recent(self.display_t):
            if self.mode == "mcr":
                return "display frames instead of MCr (another client sent MC@?)"
            return "display frames (MC@)"
        if recent(self.heartbeat_t):
            return "heartbeat only ($id: leva!) – start command not taken yet"
        if recent(self.other_t):
            return "other output"
        return "nothing received"

    def pump_text(self, now: float) -> Tuple[Optional[str], str]:
        f = self.frame
        if f is None or f.pump is None:
            return None, ""
        if self.pump_since is not None:
            return f"running {now - self.pump_since:.1f} s", "timed by this tool"
        if self.last_pump:
            d, end = self.last_pump
            return f"off · last run {d:.1f} s, {duration(now - end)} ago", "timed by this tool"
        return "off", ""

    def ready_rows(self, now: float) -> list:
        """Rows explaining the bridge's ready decision (leva2mqtt/machine.py)."""
        tr, f, cfg = self.tracker, self.frame, self.tracker.s
        rows = []
        if tr.ready and self.ready_since is not None:
            rows.append(("status", f"READY since {clock(self.ready_since)}",
                         f"{duration(now - self.ready_since)}", "32;1"))
        elif tr.mode != MODE_AWAKE:
            rows.append(("status", "not ready", tr.mode or "offline"))
        else:
            if tr.steam:
                why = "steam mode"
            elif tr.state in ("brewing", "hot_water"):
                why = tr.state.replace("_", " ")
            elif not tr.at_temperature:
                why = f"boiler not within ±{cfg.at_temp_band:g} K"
            elif len(tr.heater_avgs) < cfg.ready_samples:
                missing = cfg.ready_samples - len(tr.heater_avgs)
                why = f"{missing} more per-minute average{'s' if missing > 1 else ''} needed"
            else:
                why = "heater average not settled yet"
            rows.append(("status", "not ready", f"waiting: {why}", "33"))
        grace = tr.ready_grace_until - now
        if tr.ready and 0 < grace <= cfg.ready_grace:
            rows.append(("recovery", f"{grace:.0f} s left",
                         f"boiler may stay low after a shot / hot water ({cfg.ready_grace:g} s)"))
        if f is not None and f.temperature is not None and f.setpoint is not None:
            dev = f.temperature - f.setpoint
            rows.append(("boiler at temp.", f"{'yes' if tr.at_temperature else 'no'} ({dev:+.2f} K)",
                         f"band ±{cfg.at_temp_band:g} K"))
        if tr.heater_avgs:
            rows.append(("heater averages", " · ".join(f"{v:.1f}" for v in tr.heater_avgs) + " %",
                         f"last {cfg.ready_samples} per-minute values, oldest first"))
        spread = tr.heater_avg_spread
        if spread is not None:
            ok = spread < cfg.ready_spread
            rows.append(("spread", f"{spread:.1f} pp {'<' if ok else '≥'} {cfg.ready_spread:.1f}",
                         "settled" if ok else "not settled"))
        nxt = (tr.heater_avg_updated + cfg.avg_period
               if tr.heater_avg_updated is not None else None)
        if nxt is not None and not tr.ready:
            rows.append(("next average", f"in ~{max(0, nxt - now):.0f} s",
                         "ready is decided at each update (estimate)"))
        if self.last_ready_t is not None:
            rows.append(("firmware READY", clock(self.last_ready_t),
                         "display frames only, shown for 10 s"))
        return rows

    def sections(self, now: float) -> List[Tuple[str, List[Tuple[str, Optional[str], str]]]]:
        """[(title, [(label, value, note)])]; rows with value None are left out later."""
        out = []
        f = self.frame
        if f is not None:
            age = now - self.frame_t
            stale = f"  (last frame {duration(age)} ago)" if age > WATCHDOG_S else ""
            pump, pump_note = self.pump_text(now)
            mode = {"PID": "awake (PID)", "ECO": "eco", None: "standby"}.get(f.mode, f.mode)
            out.append(("Machine" + stale, [
                ("power mode", mode, ""),
                ("state", self.tracker.state.replace("_", " "),
                 "bridge rules, thresholds unverified"),
                ("pump / brew", pump, pump_note),
            ]))
            out.append(("Ready  (bridge rule, docs/detection-rules.md §3, unverified)",
                        self.ready_rows(now)))
            dev = (f.temperature - f.setpoint
                   if f.temperature is not None and f.setpoint is not None else None)
            out.append(("Boiler", [
                ("temperature", num(f.temperature, ".2f", "°C"), ""),
                ("setpoint", num(f.setpoint, ".1f", "°C"), "eco setpoint" if f.eco else ""),
                ("deviation", num(dev, "+.2f", "K"), ""),
                ("rate of change", num(f.temperature_rate, "+.4f"), "unit unverified"),
                ("heater", num(f.heater, "d", "%"), ""),
                ("heater avg 1 min", num(f.heater_avg, ".1f", "%"), "updated about once a minute"),
            ]))
            out.append(("PID", [
                ("terms", joined([("P", f.pid_p, ".1f"), ("I", f.pid_i, ".1f"),
                                  ("D", f.pid_d, ".1f")]), ""),
                ("constants", joined([("Kc", f.pid_kc, ".1f"), ("Ti", f.pid_ti, ".1f"),
                                      ("Td", f.pid_td, ".1f")]), ""),
            ]))
            out.append(("Pressure", [
                ("brew", num(f.pressure, ".2f", "bar"), "current value unverified"),
                ("min / max", joined([("min", f.pressure_min, ".2f"),
                                      ("max", f.pressure_max, ".2f")], "bar"), "unverified"),
                ("profile target", num(f.target_pressure, ".2f", "bar"), ""),
            ]))
            out.append(("Flow meter", [
                ("volume", num(f.volume, "d", "ml"), "since pump start, ~6 % low (unverified)"),
                ("pulses", num(f.pulses, "d"), "~2.1 per ml"),
                ("rate", num(f.flow_rate, "d", "ml/min"), ""),
            ]))
            out.append(("Scale", [
                ("weight", num(f.weight, ".1f", "g"), ""),
                ("flow", num(f.flow_weight, ".2f", "g/s"), ""),
            ]))
            out.append(("Profile", [("name", f.profile, "selected"),
                                    ("flow target", num(f.flow_target, "d", "ml/min"), "flow tracking")]))

        d = self.display
        show_display = d is not None and (self.mode == "display"
                                          or now - self.display_t <= DISPLAY_SHOW_S)
        latched = []
        if self.last_alert is not None:
            name, t = self.last_alert
            latched.append(("last alert", f"{name} at {clock(t)}",
                            "" if name == "BACKFL" else "from the manual, not seen yet"))
        if show_display or latched:
            rows = []
            if show_display:
                age = now - self.display_t
                rows += [("screen", d.describe(), f"{duration(age)} ago" if age > 2 else ""),
                         ("text", d.text or "(empty)", "")]
            out.append(("Display (MC@)", rows + latched))
        return out


# --- output ---------------------------------------------------------------------

class Style:
    def __init__(self, on: bool):
        self.on = on

    def __call__(self, code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if self.on and text else text


def screen_lines(m: Monitor, now: float, raw: bool, width: int, st: Style) -> List[str]:
    lines = []
    head = f"leva! monitor  {m.host}:{m.port}  mode {m.mode}"
    lines.append(st("1", head + clock(now).rjust(max(1, width - len(head)))))
    if m.sock and m.connected_since:
        link = f"connected {duration(now - m.connected_since)}"
    elif m.offline_since:
        link = f"offline {duration(now - m.offline_since)}"
    else:
        link = "connecting"
    status = f"link {link}  ·  stream {m.stream(now)}"
    if m.resends:
        status += f"  ·  start command re-sent {m.resends}×"
    stream = m.stream(now)
    warn = not stream.startswith("MCr") and not (m.mode == "display" and stream.startswith("display"))
    lines.append(st("33", status[:width]) if warn else status[:width])
    if m.frame is None and m.display is None:
        lines += ["", "waiting for data …"]

    label_w, value_w = 18, 26
    for title, rows in m.sections(now):
        rows = [r for r in rows if r[1] is not None]
        if not rows:
            continue
        lines += ["", st("1", title[:width])]
        for row in rows:
            label, value, note = row[:3]
            style = row[3] if len(row) > 3 else ""
            head = f"  {label:<{label_w}}"[:width]
            cell = f"{value:<{value_w}}"[: max(0, width - len(head))]
            room = width - len(head) - len(cell)
            text = head + (st(style, cell.rstrip()) + cell[len(cell.rstrip()):] if style else cell)
            if note and room > 3:
                lines.append(text + st("2", note[:room]))
            else:
                lines.append(text.rstrip())

    if raw and m.frame is not None:
        lines += ["", st("1", "Raw MCr frame")]
        r = m.frame.raw
        step = max(10, ((width - 6) // 10) * 10)
        for i in range(0, len(r), step):
            lines.append(f"  {i:3d} " + r[i:i + step].replace("\t", " "))
    if m.events:
        lines += ["", st("1", "Events")]
        for t, text in list(m.events)[-5:]:
            lines.append(st("2", f"  {clock(t)}  {text}"[:width]))
    lines.append("")
    lines.append(st("2", "Ctrl+C to stop"))
    return lines


def compact_line(m: Monitor, now: float) -> str:
    parts = [clock(now)]
    f = m.frame
    stream = m.stream(now)
    if not stream.startswith("MCr"):
        parts.append(f"[{stream}]")
    if f is not None and now - m.frame_t <= WATCHDOG_S:
        mode = {"PID": "awake", "ECO": "eco", None: "standby"}.get(f.mode, f.mode)
        parts.append(f"{mode}/{m.tracker.state}")
        tok = []
        for label, v, fmt, unit in (
            ("T", f.temperature, ".2f", ""), ("SP", f.setpoint, ".1f", ""),
            ("H", f.heater, "d", "%"), ("avg", f.heater_avg, ".1f", "%"),
            ("p", f.pressure, ".2f", "bar"), ("tgt", f.target_pressure, ".2f", "bar"),
            ("vol", f.volume, "d", "ml"), ("", f.flow_rate, "d", "ml/min"),
            ("w", f.weight, ".1f", "g"), ("", f.flow_weight, ".2f", "g/s"),
        ):
            if v is not None:
                tok.append(f"{label} {v:{fmt}}{unit}".strip() if label else f"{v:{fmt}}{unit}")
        parts.append(" ".join(tok))
        tr = m.tracker
        if tr.ready:
            parts.append("READY")
        elif tr.heater_avgs:
            spread = tr.heater_avg_spread
            parts.append("avgs " + "/".join(f"{v:.1f}" for v in tr.heater_avgs)
                         + (f" spread {spread:.1f}" if spread is not None else ""))
        pump, _ = m.pump_text(now)
        if m.pump_since is not None and pump:
            parts.append(f"pump {pump}")
    if m.display is not None and now - m.display_t <= STREAM_WINDOW_S:
        parts.append(f"<{m.display.code}> {m.display.text}")
    return "  ".join(p for p in parts if p)


def main():
    ap = argparse.ArgumentParser(description="Live view of the ito / leva! telnet stream.")
    ap.add_argument("host")
    ap.add_argument("--port", type=int, default=PORT, help="default 23 (fake_ito.py: 2300)")
    ap.add_argument("--mode", choices=START, default="mcr",
                    help="start command: mcr = MCr (default), display = MC@, none = nothing")
    ap.add_argument("--interval", type=float, default=1.0, help="seconds between updates")
    ap.add_argument("--lines", action="store_true", help="one line per update instead of a screen")
    ap.add_argument("--raw", action="store_true", help="also show the raw MCr frame")
    ap.add_argument("--no-keepalive", action="store_true", help="never send the keepalive newline")
    ap.add_argument("--no-resend", action="store_true",
                    help="do not re-send the start command while no wanted frames arrive")
    ap.add_argument("--wake", action="store_true",
                    help="send MCcREPORT SP1 once after connecting (wakes the machine, it beeps)")
    ap.add_argument("--no-color", action="store_true")
    a = ap.parse_args()

    tty = sys.stdout.isatty()
    screen = tty and not a.lines
    st = Style(tty and not a.no_color and "NO_COLOR" not in os.environ)
    m = Monitor(a.host, a.port, a.mode, not a.no_keepalive, not a.no_resend, a.wake)
    m.event(f"options: mode={a.mode} keepalive={not a.no_keepalive} "
            f"resend={not a.no_resend} wake={a.wake}")

    def render(mon: Monitor, now: float) -> None:
        if screen:
            size = shutil.get_terminal_size((100, 40))
            out = screen_lines(mon, now, a.raw, size.columns, st)[: size.lines - 1]
            sys.stdout.write("\x1b[H" + "\n".join(line + "\x1b[K" for line in out) + "\x1b[J")
            mon.new_events.clear()
        else:
            for e in mon.new_events:
                print(st("2", e))
            mon.new_events.clear()
            print(compact_line(mon, now))
        sys.stdout.flush()

    if screen:
        sys.stdout.write("\x1b[?1049h\x1b[?25l")   # alternate screen, hide cursor
    try:
        m.run(max(0.2, a.interval), render)
    except KeyboardInterrupt:
        pass
    except BrokenPipeError:   # output piped into e.g. head, which has quit
        sys.stdout = open(os.devnull, "w")
    finally:
        if screen:
            sys.stdout.write("\x1b[?25h\x1b[?1049l")
            sys.stdout.flush()
        if m.sock:
            m.sock.close()
    print(f"stopped, {m.lines_rx} lines received")


if __name__ == "__main__":
    main()
