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


def feed_minutes(tracker, temps_and_avgs, t0=0.0):
    """One minute of frames per (temperature, per-minute average) pair."""
    t = t0
    for temp, avg in temps_and_avgs:
        for _ in range(240):
            tracker.update(parse(frame(temp=temp, heater_avg=avg)), t)
            t += 0.25
    return t


def test_ready_follows_heater_average():
    # cold start 13.10.: 22.4 -> 16.0 -> 10.4 -> 10.7 -> 10.1, beep after the 10.1 update
    tr = Tracker()
    feed_minutes(tr, [(89.9, 22.4), (90.5, 16.0), (90.3, 10.4), (90.05, 10.7)])
    assert tr.at_temperature and not tr.ready
    feed_minutes(tr, [(90.25, 10.1)])
    assert tr.ready and tr.state == READY


def test_ready_spread_must_be_below_one_point():
    # warm power cycle 12.10.: 3.7 -> 4.7 -> 4.1 (spread 1.0, no beep) -> 4.7 (beep)
    tr = Tracker()
    feed_minutes(tr, [(89.95, 3.7), (90.15, 4.7), (89.9, 4.1)])
    assert not tr.ready
    feed_minutes(tr, [(90.05, 4.7)])
    assert tr.ready


def test_ready_needs_boiler_at_temperature():
    tr = Tracker()
    feed_minutes(tr, [(88.0, 10.0), (88.2, 10.2), (88.4, 10.1)])
    assert not tr.ready and not tr.at_temperature


def test_unchanged_average_counts_as_update():
    tr = Tracker()
    feed_minutes(tr, [(90.0, 5.0), (90.0, 5.0), (90.0, 5.0)])
    assert tr.ready


def test_ready_kept_during_shot_lost_after():
    tr = Tracker()
    t = feed_minutes(tr, [(90.0, 5.0), (90.1, 5.2), (90.0, 5.1)])
    assert tr.ready
    t = feed(tr, [frame(pump="*", temp=86.5, heater_avg=5.1)] * 40, t0=t)
    assert tr.ready and tr.state == BREWING
    # recovery time: the boiler may stay low for ready_grace seconds after the shot
    t = feed(tr, [frame(temp=86.5, heater_avg=5.1)] * int(tr.s.ready_grace / 0.25 - 4), t0=t)
    assert tr.ready and tr.state == READY
    feed(tr, [frame(temp=86.5, heater_avg=5.1)] * 8, t0=t)
    assert not tr.ready and tr.state == HEATING


def test_ready_survives_recovered_shot():
    tr = Tracker()
    t = feed_minutes(tr, [(90.0, 5.0), (90.1, 5.2), (90.0, 5.1)])
    t = feed(tr, [frame(pump="*", temp=86.0, heater_avg=5.1)] * 80, t0=t)
    t = feed(tr, [frame(temp=87.5 + i * 0.01, heater_avg=5.1) for i in range(300)], t0=t)
    feed(tr, [frame(temp=89.8, heater_avg=5.1)] * 400, t0=t)
    assert tr.ready and tr.state == READY


def test_ready_kept_during_hot_water():
    tr = Tracker()
    t = feed_minutes(tr, [(90.0, 5.0), (90.1, 5.2), (90.0, 5.1)], t0=0.0)
    t = feed(tr, [frame(temp=90.0, volume=10, heater_avg=5.1)] * 4, t0=t)
    t = feed(tr, [frame(temp=90.0 - i * 0.1, volume=10 + i * 2, heater_avg=5.1)
                  for i in range(60)], t0=t)
    assert tr.state == HOT_WATER and tr.ready
    feed(tr, [frame(temp=84.0, volume=130, heater_avg=5.1)] * 200, t0=t)
    assert tr.ready


def test_ready_reset_by_standby_and_steam():
    tr = Tracker()
    t = feed_minutes(tr, [(90.0, 5.0), (90.1, 5.2), (90.0, 5.1)])
    t = feed(tr, [frame(standby=True)], t0=t)
    assert not tr.ready
    t = feed_minutes(tr, [(90.0, 5.0), (90.1, 5.2), (90.0, 5.1)], t0=t)
    assert tr.ready
    feed(tr, [frame(temp=105, heater_avg=0.0)], t0=t)
    assert not tr.ready and tr.state == STEAM


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


def test_real_frame_heater_at_full_power():
    # captured 2026-10-13, cold start: setpoint 090.0, heater 100 %
    raw = ("{ -1PID020.80090.01000650.30000.00000.00000.0000.0000009.4058.0014.5020.0"
           "?????OFF" + "?" * 200)
    raw = raw[:288].ljust(288, "?") + "}"
    f = parse(raw)
    assert f.setpoint == 90.0 and f.heater == 100 and f.temperature == 20.8
