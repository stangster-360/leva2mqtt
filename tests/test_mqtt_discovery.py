import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from framegen import frame  # noqa: E402

from leva2mqtt.config import load  # noqa: E402
from leva2mqtt.frame import parse  # noqa: E402
from leva2mqtt.machine import Tracker  # noqa: E402
from leva2mqtt.mqtt import MqttOutput  # noqa: E402


def _output():
    cfg = load("/nonexistent.yaml", {"MACHINE_HOST": "ito", "MQTT_HOST": "broker"})
    out = MqttOutput(cfg, asyncio.new_event_loop(), lambda c: None, lambda k, v: None, {})
    out.published = []
    out._pub = lambda topic, payload, retain=False, qos=0: out.published.append((topic, payload))
    out._connected = True
    return out


def _configs(out):
    return {t.split("/")[-2]: p for t, p in out.published if t.endswith("/config")}


def test_new_sensors_are_announced_once_seen():
    out = _output()
    out.publish_live({"pump_time": 12.3, "heater_avg_spread": 0.6,
                      "heater_avgs": [10.4, 10.7, 10.1], "pid_kc": 9.4, "pid_ti": None})
    c = _configs(out)
    assert c["pump_time"]["unit_of_measurement"] == "s"
    assert c["pump_time"]["device_class"] == "duration"
    spread = c["heater_avg_spread"]
    assert spread["entity_category"] == "diagnostic"
    assert "heater_averages" in spread["json_attributes_template"]
    assert c["pid_kc"]["enabled_by_default"] is False and "state_class" not in c["pid_kc"]
    assert "pid_ti" not in c            # no value yet -> not announced
    state = json.loads(json.dumps(out.published[-1][1]))
    assert state["heater_avgs"] == [10.4, 10.7, 10.1]


def test_pump_time_runs_and_keeps_last_run():
    tr = Tracker()
    t = 0.0
    for fr in [frame()] * 4:
        tr.update(parse(fr), t)
        t += 0.25
    assert tr.pump_time(t) == 0.0
    start = t
    for fr in [frame(pump="*")] * 40:
        tr.update(parse(fr), t)
        t += 0.25
    assert abs(tr.pump_time(t - 0.25) - (t - 0.25 - start)) < 1e-9
    tr.update(parse(frame()), t)
    assert tr.pump_time(t + 30) == 10.0
    tr.update(parse(frame(standby=True)), t + 31)
    assert tr.pump_time(t + 31) is None
