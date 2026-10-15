"""Diagnostic entities, shot extras, dropped-frame counter, alarm bell, connection slots."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from framegen import _put, _sealed, frame  # noqa: E402

from leva2mqtt import frame as frame_mod
from leva2mqtt import link as link_mod
from leva2mqtt.bridge import Bridge
from leva2mqtt.config import load
from leva2mqtt.display import parse_display
from leva2mqtt.frame import COUNTERS, parse
from leva2mqtt.link import Link
from leva2mqtt.modes import ModeController
from leva2mqtt.outbox import Outbox
from leva2mqtt.shots import SHOT, ShotRecorder
from leva2mqtt.visualizer import extra_series, to_decent_json

# captured 2026-10-12 (shots), pump running in the lever profile
REAL_PUMP = ('{ *1PID089.90090.0037-000.60011.80025.50000.0-00.1875009.4058.0014.5020.0025.6OFF' + "?" * 71 + '07500159257001.0' + "?" * 24 + '07.8407.7407.6207.94042055-008-005005.002.98     9BARGEN.LEVER                  GEN.LEVER???~ A4}')


def bridge():
    cfg = load("/nonexistent", {"MACHINE_HOST": "127.0.0.1", "SHOTS_DIRECTORY": "/tmp/x"})
    return Bridge(cfg)


def test_real_pump_frame_fields():
    f = parse(REAL_PUMP)
    assert f is not None and f.pump
    assert (f.target_pressure, f.pressure, f.pressure_min, f.pressure_max) == (7.84, 7.74, 7.62, 7.94)
    assert (f.pump_phase, f.pump_phase_bias, f.pump_phase_p, f.pump_phase_i) == (42, 55, -8, -5)
    assert (f.weight, f.flow_weight) == (5.0, 2.98)
    assert f.profile == "GEN.LEVER" and f.profile1 == "9BAR" and f.profile2 == "GEN.LEVER"
    assert f.mode2 == "OFF" and f.temperature_pcb is None and f.flow_target is None


def test_live_values_new_keys_and_pid2_hidden():
    b = bridge()
    v = b._live(parse(REAL_PUMP), 0.0)
    assert v["pump_phase"] == 42 and v["pressure_min"] == 7.62 and v["pid_windup"] == 20.0
    assert v["profiles"] == ["9BAR", "GEN.LEVER"]
    assert all(v[k] is None for k in ("mode2", "temperature2", "pid2_p", "temperature_pcb",
                                      "temperature_aux1", "flow_target"))
    assert v["bad_frames"] == COUNTERS["bad_checksum"]


def test_pid2_published_when_in_use():
    raw = list(frame())
    _put(raw, 78, "PID")
    _put(raw, 81, "120.5")
    _put(raw, 87, "125.0")
    _put(raw, 92, "040")
    v = bridge()._live(parse(_sealed(raw)), 0.0)
    assert (v["mode2"], v["temperature2"], v["setpoint2"], v["heater2"]) == ("PID", 120.5, 125.0, 40)


def test_bad_checksum_is_counted():
    before = COUNTERS["bad_checksum"]
    good = frame(temp=90.0)
    broken = good.replace("090.00", "091.00", 1)
    assert parse(good) is not None and parse(broken) is None
    assert parse("<102 not a frame>") is None
    assert COUNTERS["bad_checksum"] == before + 1
    assert frame_mod.COUNTERS is COUNTERS


def test_shot_profile_from_selected_slot():
    # slot 2 holds GEN.LEVER, but 9BAR is selected: the run must carry 9BAR
    rec = ShotRecorder(tail=1)
    t, done = 0.0, None
    for i in range(60):
        done = done or rec.update(parse(frame(pump="*", pressure=8.0, weight=i * 0.5,
                                              profile="9BAR")), t)
        t += 0.25
    for _ in range(10):
        done = done or rec.update(parse(frame(pressure=5.0, profile="9BAR")), t)
        t += 0.25
    assert done.kind == SHOT and done.profile == "9BAR"


def _run_from(frames):
    rec = ShotRecorder(tail=1)
    t, done = 0.0, None
    for f in frames + [frame(pressure=5.0)] * 10:
        done = done or rec.update(parse(f), t)
        t += 0.25
    return done


def test_visualizer_flow_goal_and_extra_series():
    run = _run_from([REAL_PUMP] * 40)
    shot = to_decent_json(run)
    assert "goal" not in shot["flow"]                  # no flow target on this machine
    extra = extra_series(run)
    assert extra["pump_phase"][0] == 42 and extra["pressure_max"][0] == 7.94

    raw = list(frame(pump="*", pressure=8.0))
    _put(raw, 281, "180")                              # flow tracking target 180 ml/min
    run2 = _run_from([_sealed(raw)] * 40)
    assert run2.samples[0].flow_target == 180
    assert shot is not None and to_decent_json(run2)["flow"]["goal"][0] == 3.0


def test_extra_series_stays_in_file_not_upload(tmp_path):
    ob = Outbox(str(tmp_path), False, {})
    e = ob.add("a.json", {"version": 2}, {"kind": "shot"}, [], {"pump_phase": [42]})
    assert "leva2mqtt" not in e.shot
    data = json.load(open(tmp_path / "pending" / "a.json"))
    assert data["leva2mqtt"]["series"] == {"pump_phase": [42]}


def test_alarm_bell_counts_as_alert():
    mc = ModeController(first_delay=0)
    mc.link_up(0)
    assert mc.tick(0, idle=True) == "display"
    bell = "<102GEN.LEVER       1: 90.1ß \x04 90.0ß\x07             9% 0.9b-  0s   0ml>"
    for i in range(10):
        mc.display(parse_display(bell), 0.3 + i * 0.1)
    mc.tick(1.5, idle=True)
    assert mc.alerts == ("BELL",)
    d = parse_display("<102GEN.LEVER       1: 90.1ß \x04 90.0ß\x07BACKFL      9% 0.9b-  0s   0ml>")
    assert d.bell and d.alerts == ("BACKFL",)


def test_slots_full_after_empty_connections(monkeypatch):
    monkeypatch.setattr(link_mod, "RETRY_S", 0.05)
    events = []

    async def scenario():
        serve_frames = {"on": False}

        async def handler(reader, writer):
            if serve_frames["on"]:
                writer.write(frame().encode("latin-1") + b"\r\n")
                await writer.drain()
                await asyncio.sleep(0.3)
            writer.close()                                # 5th connection: closed at once

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        lk = Link("127.0.0.1", port, on_frame=lambda f, t: None, on_link=lambda *a: None)
        lk.on_slots = events.append
        task = asyncio.create_task(lk.run())
        await asyncio.sleep(1.0)
        assert lk.slots_full and events == [True]
        serve_frames["on"] = True
        await asyncio.sleep(1.0)
        assert not lk.slots_full and events == [True, False]
        lk.stop()
        await task
        server.close()

    asyncio.run(scenario())
