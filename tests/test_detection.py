import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from framegen import frame  # noqa: E402

from leva2mqtt.config import ConfigError, load
from leva2mqtt.frame import parse
from leva2mqtt.machine import (BREWING, ECO, HEATING, HOT_WATER, READY, STANDBY, STEAM,
                               Tracker, WakeReporter)
from leva2mqtt.shots import FLUSH, SHOT, ShotRecorder


def test_parse_awake_frame():
    f = parse(frame(temp=89.5, weight=12.3, flow_weight=2.1, pressure=8.2, target=9.0))
    assert f.mode == "PID" and f.awake and not f.pump
    assert f.temperature == 89.5 and f.setpoint == 90.0
    assert f.weight == 12.3 and f.flow_weight == 2.1
    assert f.pressure == 8.2 and f.target_pressure == 9.0
    assert f.profile == "GEN.LEVER"


def test_missing_sensors_are_none():
    f = parse(frame(weight=None, flow_weight=None, target=None))
    assert f.weight is None and f.flow_weight is None and f.target_pressure is None


def test_standby_frame():
    f = parse(frame(standby=True))
    assert f.standby and f.pump is None and f.temperature is None


def test_not_a_frame():
    assert parse("<102GEN.LEVER  1: 90.1ß>") is None
    assert parse("$id: leva! (3.0)") is None


def feed(tracker, frames, t0=0.0, dt=0.25):
    t = t0
    for fr in frames:
        tracker.update(parse(fr), t)
        t += dt
    return t


def test_states():
    tr = Tracker()
    feed(tr, [frame(standby=True)])
    assert tr.state == STANDBY
    feed(tr, [frame(mode="ECO", setpoint=69.9, temp=80)])
    assert tr.state == ECO
    feed(tr, [frame(temp=70)])
    assert tr.state == HEATING
    feed(tr, [frame(pump="*")])
    assert tr.state == BREWING
    feed(tr, [frame(temp=105)])
    assert tr.state == STEAM


def test_ready_needs_stable_temperature():
    tr = Tracker()
    t = feed(tr, [frame(temp=90.1)] * int(tr.s.ready_hold / 0.25 - 4))
    assert tr.state != READY
    feed(tr, [frame(temp=90.1)] * 8, t0=t)
    assert tr.state == READY


def test_hot_water_without_pump_flag():
    tr = Tracker()
    t = feed(tr, [frame(volume=0)] * 4)
    t = feed(tr, [frame(volume=v) for v in range(0, 40, 2)], t0=t)
    assert tr.state == HOT_WATER
    feed(tr, [frame(volume=38)] * 20, t0=t)
    assert tr.state != HOT_WATER


def test_wake_reporter_rules():
    r = WakeReporter(min_offline=5)
    r.link_up(100.0, None)                  # bridge start
    assert not r.due("awake")
    r.link_up(200.0, 198.0)                 # short drop
    assert not r.due("awake")
    r.link_up(300.0, 250.0)                 # power cycle
    assert not r.due("standby")             # never while in standby
    assert r.due("awake") and not r.due("awake")
    r.mode_change("standby", "awake", 400.0)
    assert r.due("awake")
    r.manual_report(500.0)
    r.mode_change("eco", "awake", 501.0)    # caused by the manual report
    assert not r.due("awake")
    r.mode_change(None, "awake", 600.0)     # first frame after a connect
    assert not r.due("awake")


def run_shot(rec, pressures, weights, dt=0.25):
    t, done = 0.0, None
    for p, w in zip(pressures, weights):
        done = done or rec.update(parse(frame(pump="*", pressure=p, weight=w, rate=480)), t)
        t += dt
    for _ in range(30):
        done = done or rec.update(parse(frame(pump="-", pressure=5.0, weight=weights[-1] + 2)), t)
        t += dt
    return done


def test_shot_and_flush():
    rec = ShotRecorder(tail=5)
    shot = run_shot(rec, [0.5] * 36 + [8.0] * 40, [0.0] * 36 + [w * 0.5 for w in range(40)])
    assert shot.kind == SHOT and abs(shot.duration - 19) < 0.3
    assert shot.yield_g == 0.5 * 39 + 2 and shot.peak_pressure == 8.0
    flush = run_shot(rec, [0.8] * 20, [w * 2.5 for w in range(20)])
    assert flush.kind == FLUSH


def test_config_env_overrides(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("machine:\n  host: 10.0.0.5\n  name: My Silvia\nmqtt:\n  host: broker\n")
    cfg = load(str(p), {"MQTT_PORT": "1884", "TELEGRAM_TOKEN": "x"})
    assert cfg.machine.host == "10.0.0.5" and cfg.machine.id == "my_silvia"
    assert cfg.mqtt.port == 1884 and cfg.mqtt_enabled and not cfg.telegram_enabled


def test_config_requires_host(tmp_path):
    try:
        load(str(tmp_path / "missing.yaml"), {})
    except ConfigError:
        return
    raise AssertionError("expected ConfigError")
