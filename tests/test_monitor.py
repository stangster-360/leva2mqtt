import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from framegen import frame  # noqa: E402

import monitor  # noqa: E402
from leva2mqtt.display import parse_display  # noqa: E402
from leva2mqtt.frame import parse  # noqa: E402


class _Sock:
    """Stands in for a connected socket; the monitor only checks it is set."""


def _monitor(lines, now=1000.0, mode="mcr"):
    m = monitor.Monitor("ito", 23, mode, True, True, False)
    m.sock = _Sock()
    m.connected_since = now - 60
    for i, line in enumerate(lines):
        m.handle(line, now - len(lines) * 0.25 + i * 0.25)
    return m


def _screen(m, now=1000.0):
    return "\n".join(monitor.screen_lines(m, now, False, 120, monitor.Style(False)))


def test_pid_constants_and_pressure_range():
    f = frame()
    f = f[:53] + "009.4058.0014.5" + f[68:]
    f = f[:202] + "08.1008.45" + f[212:]
    from framegen import _sealed
    p = parse(_sealed(list(f)))
    assert (p.pid_kc, p.pid_ti, p.pid_td) == (9.4, 58.0, 14.5)
    assert (p.pressure_min, p.pressure_max) == (8.1, 8.45)


def test_missing_sensors_are_left_out():
    out = _screen(_monitor([frame(weight=None, flow_weight=None, target=None)] * 8))
    assert "temperature" in out and "90.0 °C" in out
    assert "weight" not in out and "Scale" not in out and "profile target" not in out


def test_standby_shows_only_the_mode():
    out = _screen(_monitor([frame(standby=True)] * 8))
    assert "standby" in out
    for absent in ("Boiler", "PID", "Pressure", "Flow meter", "Scale", "Profile"):
        assert absent not in out


def test_pump_timer_and_last_run():
    lines = [frame()] * 4 + [frame(pump="*")] * 40 + [frame()] * 4
    m = _monitor(lines)
    value, _ = m.pump_text(1000.0)
    assert value.startswith("off · last run 10.0 s")


def test_display_frame_decoding():
    line = "<132GEN.LEVER       1: 90.1\xdf  90.0\xdf BACKFL     7% 1.8b-  0s   0.0g>"
    d = monitor.Display(parse_display(line))
    assert d.alerts == ["BACKFL"] and not d.ready
    assert "°" in d.text and "main / shot screen" in d.describe() and "click" in d.describe()
    ready = "<102GEN.LEVER       1: 89.9\xdf   READY                   6%>"
    assert monitor.Display(parse_display(ready)).ready


def test_stream_status():
    now = 1000.0
    assert monitor.Monitor("ito", 23, "mcr", True, True, False).stream(now) == "connecting"
    assert _monitor(["$id: leva! (3.0)"]).stream(now).startswith("heartbeat only")
    assert "another client" in _monitor(["<102GEN.LEVER  1: 90.1\xdf>"]).stream(now)
    assert _monitor(["<102GEN.LEVER>"], mode="display").stream(now).startswith("display frames")
    assert _monitor([frame()] * 8).stream(now).startswith("MCr")


def test_compact_line_omits_missing_values():
    line = monitor.compact_line(_monitor([frame(weight=None, flow_weight=None)] * 8), 1000.0)
    assert "T 90.00" in line and "w " not in line and "g/s" not in line


def test_ready_section_explains_the_decision():
    def minute(avg):
        return [frame(temp=90.1, heater_avg=avg)] * 240
    m = _monitor(minute(16.0) + minute(10.4) + minute(10.7), now=180.0)
    out = _screen(m, now=180.0)
    assert "not ready" in out and "16.0 · 10.4 · 10.7 %" in out and "5.6 pp" in out
    m = _monitor(minute(16.0) + minute(10.4) + minute(10.7) + minute(10.1), now=240.0)
    out = _screen(m, now=240.0)
    assert "READY since" in out and "0.6 pp < 1.0" in out
    assert "READY" in monitor.compact_line(m, 240.0)


def test_display_shot_screen_from_capture():
    # captured 2026-10-13 (flush): pressure is the first number of line 4, `9.0b` is the target
    from leva2mqtt.display import parse_display
    d = parse_display("<102 0s  0.0g       1: 89.8\xdf \x04 90.0\xdf  7%    0s R=0.0 1.5  9.0b  0.0g>")
    assert d.shot and d.pressure == 1.5 and d.weight == 0.0 and d.temperature == 89.8
    d = parse_display("<192 0s 51.8g       1: 89.6\xdf \x04 90.0\xdf  8%    0s R=8.1 0.4   0\xdf  51.8g>")
    assert d.shot and d.input_event and d.pressure == 0.4 and d.weight == 51.8 and d.heater == 8
