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


def test_curve_ends_at_pump_stop_yield_includes_drip():
    # seen 16.10.2026: pressure at the sensor stays at ~5 bar after the pump stop
    # (boiler sealed off), drip into the cup ends 1-2 s later
    from leva2mqtt.visualizer import extra_series, to_decent_json
    rec = ShotRecorder(tail=5)
    shot = run_shot(rec, [0.5] * 36 + [8.0] * 40, [0.0] * 36 + [w * 0.5 for w in range(40)])
    assert len(shot.samples) > len(shot.curve)
    assert shot.curve[-1].t == shot.duration and not shot.curve[-1].pump
    data = to_decent_json(shot)
    assert data["elapsed"][-1] == round(shot.duration, 3)
    assert len(data["pressure"]["pressure"]) == len(data["elapsed"])
    assert data["app"]["data"]["settings"]["drink_weight"] == str(round(shot.yield_g, 1))
    extra = extra_series(shot)
    assert extra is None or all(len(v) == len(data["elapsed"]) for v in extra.values())


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


def test_checksum_from_captures():
    from leva2mqtt.frame import checksum_ok
    # standby frame as captured, and the same frame with lost characters (seen once)
    ok = "{ ?1" + "?" * 159 + "001.0" + "?" * 116 + "~ 59}"
    assert len(ok) == 289 and checksum_ok(ok) and parse(ok) is not None
    broken = "{ ?1" + "?" * 88 + "01.0" + "?" * 112 + "~ 59}"
    assert not checksum_ok(broken) and parse(broken) is None


SHOT_FRAME = ("{ *1PID089.40090.00270005.80004.00017.00000.0-00.1250009.4058.0014.5020.0003.4OFF"
              + "?" * 71 + "06100129250001.0" + "?" * 24
              + "08.3208.2207.9208.60020052-008-022011.202.37     9BARGEN.LEVER"
              + " " * 18 + "GEN.LEVER???~ EB}")


def test_all_fields_of_a_captured_shot_frame():
    # captured 2026-10-13 during a shot; field order as in the leva! Status Monitor XML
    assert len(SHOT_FRAME) == 289
    f = parse(SHOT_FRAME)
    assert f.pump and f.mode == "PID" and f.temperature == 89.4 and f.setpoint == 90.0
    assert (f.heater, f.pid_p, f.pid_i, f.pid_d, f.pid_ff) == (27, 5.8, 4.0, 17.0, 0.0)
    assert (f.pid_kc, f.pid_ti, f.pid_td, f.pid_windup, f.heater_avg) == (9.4, 58.0, 14.5, 20.0, 3.4)
    assert f.mode2 == "OFF" and f.temperature2 is None and f.temperature_pcb is None
    assert (f.volume, f.pulses, f.flow_rate) == (61, 129, 250)
    assert (f.target_pressure, f.pressure, f.pressure_min, f.pressure_max) == (8.32, 8.22, 7.92, 8.6)
    assert (f.pump_phase, f.pump_phase_bias, f.pump_phase_p, f.pump_phase_i) == (20, 52, -8, -22)
    assert (f.weight, f.flow_weight) == (11.2, 2.37)
    assert f.pid_selected == 1 and f.increment == 1.0
    assert (f.profile1, f.profile2, f.profile3, f.profile4) == ("9BAR", "GEN.LEVER", None, None)
    assert f.profile == "GEN.LEVER" and f.flow_target is None


def test_frame_from_display_frame():
    from leva2mqtt.bridge import _frame_from_display
    from leva2mqtt.display import parse_display
    d = parse_display("<102GEN.LEVER       1: 90.1\xdf \x04 90.0\xdf\x03             9% 0.4b-  0s 56.2g>")
    f = _frame_from_display(d)
    assert f.temperature == 90.1 and f.heater == 9 and f.weight == 56.2 and f.pid_kc is None
