"""MQTT output with Home Assistant discovery (optional; enabled when mqtt.host is set).

Topics (base = <topic_prefix>/<machine id>):

  base/bridge                 online/offline, retained, last will
  base/availability           online/offline, retained: frames arrive from the machine
  base/state                  live values as JSON
  base/last_shot              last shot as JSON, retained
  base/event                  every finished pump run as JSON (shot, flush, …),
                              and shot_uploaded with the Visualizer URL
  base/pending                number of shots waiting for upload / message, retained
  base/command/<button>       PRESS: wake, eco, standby, report, refresh_coffee_bags
  base/settings/<key>         runtime settings, retained (restored on start)
  base/settings/<key>/set     set a runtime setting

Discovery for a sensor is only published once its value has been seen in a frame, so
sensors a machine does not have never show up in Home Assistant.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional

import paho.mqtt.client as mqtt

from . import __version__
from .config import Config
from .machine import STATES

log = logging.getLogger(__name__)

# key: (name, unit, device_class, state_class, icon, entity_category, enabled_by_default, precision)
SENSORS: dict[str, tuple] = {
    "temperature": ("Boiler temperature", "°C", "temperature", "measurement", None, None, True, 1),
    "setpoint": ("Setpoint", "°C", "temperature", "measurement", None, None, True, 1),
    "heater": ("Heater output", "%", None, "measurement", "mdi:radiator", None, True, 0),
    "heater_avg": ("Heater output last minute", "%", None, "measurement", "mdi:radiator", "diagnostic", True, 1),
    "pressure": ("Brew pressure", "bar", "pressure", "measurement", None, None, True, 1),
    "target_pressure": ("Target pressure", "bar", "pressure", "measurement", None, None, True, 1),
    "weight": ("Weight", "g", "weight", "measurement", None, None, True, 1),
    "flow_weight": ("Flow by weight", "g/s", None, "measurement", "mdi:water", None, True, 1),
    "flow_rate": ("Flow meter rate", "mL/min", "volume_flow_rate", "measurement", None, None, True, 0),
    "volume": ("Flow meter volume", "mL", "volume", "measurement", None, None, True, 0),
    "mode": ("Control mode", None, None, None, "mdi:tune", "diagnostic", True, None),
    "profile": ("Profile", None, None, None, "mdi:chart-bell-curve", None, True, None),
    "pid_p": ("PID P term", None, None, "measurement", None, "diagnostic", False, 1),
    "pid_i": ("PID I term", None, None, "measurement", None, "diagnostic", False, 1),
    "pid_d": ("PID D term", None, None, "measurement", None, "diagnostic", False, 1),
    "pid_kc": ("PID Kc", None, None, None, None, "diagnostic", False, 1),
    "pid_ti": ("PID Ti", None, None, None, None, "diagnostic", False, 1),
    "pid_td": ("PID Td", None, None, None, None, "diagnostic", False, 1),
    # bridge's own clock for the '*' flag: running time, else the last run (frame has no timer)
    "pump_time": ("Pump time", "s", "duration", "measurement", "mdi:timer-outline", None, True, 1),
    # spread of the last three per-minute heater averages in %-points (ready needs < 1.0);
    # the averages themselves are attributes
    "heater_avg_spread": ("Heater average spread", "%", None, "measurement",
                          "mdi:arrow-expand-vertical", "diagnostic", True, 1),
    "alert": ("Maintenance alerts", None, None, None, "mdi:alert-circle-outline", None, True, None),
}

# key: (name, device_class, icon)
BINARY_SENSORS: dict[str, tuple] = {
    "pump": ("Pump", "running", "mdi:pump"),
    "heating": ("Heating up", "heat", None),
    "at_temperature": ("Boiler at temperature", None, "mdi:thermometer-check"),
    "ready": ("Ready", None, "mdi:coffee-maker-check"),
    "steam": ("Steam mode", None, "mdi:kettle-steam"),
    "hot_water": ("Hot water", "running", "mdi:water-boiler"),
    "problem": ("Maintenance alert", "problem", None),
    "other_client": ("Other client connected", None, "mdi:cellphone-link"),
}
DIAGNOSTIC_BINARY = {"other_client"}

BUTTONS: dict[str, tuple[str, str]] = {
    "wake": ("Wake", "mdi:power"),
    "eco": ("Eco", "mdi:leaf"),
    "standby": ("Standby", "mdi:power-sleep"),
    "report": ("Beep setpoint", "mdi:volume-high"),
}
NO_BAG = "None"     # select option: shot not linked to a coffee bag


class MqttOutput:
    def __init__(self, cfg: Config, loop: asyncio.AbstractEventLoop,
                 on_command: Callable[[str], None],
                 on_setting: Callable[[str, Any], None],
                 settings: dict[str, Any]):
        self.cfg = cfg
        self.loop = loop
        self.on_command = on_command
        self.on_setting = on_setting
        self.settings = settings            # current runtime settings (owned by the bridge)
        m = cfg.machine
        self.base = f"{cfg.mqtt.topic_prefix}/{m.id}"
        self.disc = cfg.mqtt.discovery_prefix
        self.uid = f"leva2mqtt_{m.id}"
        self.device = {
            "identifiers": [self.uid],
            "name": m.name,
            "manufacturer": "ito / leva!",
            "model": "ito module, leva! firmware",
            "sw_version": f"leva2mqtt {__version__}",
        }
        self.seen: set[str] = set()         # sensor keys with a value at least once
        self._announced: set[str] = set()
        self._machine_online = False
        self._pending = 0
        self._bag_options: Optional[list[str]] = None   # None = no coffee bag entity
        self._bag_announced = False
        self._connected = False
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f"leva2mqtt-{m.id}", clean_session=True)
        if cfg.mqtt.username:
            self.client.username_pw_set(cfg.mqtt.username, cfg.mqtt.password or None)
        if cfg.mqtt.tls:
            self.client.tls_set()
        self.client.will_set(f"{self.base}/bridge", "offline", qos=1, retain=True)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.reconnect_delay_set(1, 30)

    # --- lifecycle -------------------------------------------------------

    def start(self) -> None:
        self.client.connect_async(self.cfg.mqtt.host, self.cfg.mqtt.port, keepalive=30)
        self.client.loop_start()

    def stop(self) -> None:
        self._pub(f"{self.base}/bridge", "offline", retain=True)
        self.client.disconnect()
        self.client.loop_stop()

    @property
    def connected(self) -> bool:
        return self._connected

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
            return
        log.info("MQTT connected to %s:%s", self.cfg.mqtt.host, self.cfg.mqtt.port)
        self._connected = True
        client.subscribe([(f"{self.base}/command/+", 1), (f"{self.base}/settings/+", 1),
                          (f"{self.base}/settings/+/set", 1), (f"{self.disc}/status", 1)])
        self.loop.call_soon_threadsafe(self._announce_all)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self._connected = False
        log.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            payload = msg.payload.decode("utf-8")
        except UnicodeDecodeError:
            return
        self.loop.call_soon_threadsafe(self._handle, msg.topic, payload, msg.retain)

    def _handle(self, topic: str, payload: str, retained: bool) -> None:
        if topic == f"{self.disc}/status":
            if payload == "online":
                self._announced.clear()
                self._announce_all()
            return
        rest = topic[len(self.base) + 1:]
        parts = rest.split("/")
        if parts[0] == "command" and len(parts) == 2 and not retained:
            if parts[1] in BUTTONS or (parts[1] == "refresh_coffee_bags"
                                       and self.cfg.visualizer_enabled):
                self.on_command(parts[1])
        elif parts[0] == "settings" and len(parts) == 2:
            if retained:   # restore after a restart
                self.on_setting(parts[1], payload)
        elif parts[0] == "settings" and len(parts) == 3 and parts[2] == "set":
            self.on_setting(parts[1], payload)

    # --- publishing ------------------------------------------------------

    def _pub(self, topic: str, payload: Any, retain: bool = False, qos: int = 0) -> None:
        if not isinstance(payload, str):
            payload = json.dumps(payload, separators=(",", ":"))
        self.client.publish(topic, payload, qos=qos, retain=retain)

    def publish_live(self, values: dict) -> None:
        for key, v in values.items():
            if v is not None and key in SENSORS and key not in self.seen:
                self.seen.add(key)
                if self._connected:
                    self._announce_sensor(key)
        self._pub(f"{self.base}/state", values)

    def publish_machine(self, online: bool) -> None:
        self._machine_online = online
        self._pub(f"{self.base}/availability", "online" if online else "offline", retain=True, qos=1)

    def publish_setting(self, key: str, value: Any) -> None:
        if isinstance(value, bool):
            value = "ON" if value else "OFF"
        self._pub(f"{self.base}/settings/{key}", "" if value is None else str(value), retain=True, qos=1)

    def publish_run(self, record: dict) -> None:
        self._pub(f"{self.base}/event", {"event": "run_finished", **record}, qos=1)
        if record.get("kind") == "shot":
            self._pub(f"{self.base}/last_shot", record, retain=True, qos=1)

    def publish_event(self, payload: dict) -> None:
        self._pub(f"{self.base}/event", payload, qos=1)

    def publish_pending(self, count: int) -> None:
        self._pending = count
        self._pub(f"{self.base}/pending", str(count), retain=True, qos=1)

    def update_record(self, record: dict) -> None:
        """Republish the last shot after the Visualizer URL is known."""
        self._pub(f"{self.base}/last_shot", record, retain=True, qos=1)

    def set_coffee_bags(self, labels: Optional[list[str]]) -> None:
        """Options for the coffee bag select; None removes the entity."""
        self._bag_options = None if labels is None else [NO_BAG, *labels]
        if self._connected:
            self._announce_bags()

    # --- discovery -------------------------------------------------------

    def _availability(self, machine: bool = True) -> dict:
        av = [{"topic": f"{self.base}/bridge"}]
        if machine:
            av.append({"topic": f"{self.base}/availability"})
        return {"availability": av, "availability_mode": "all"}

    def _config(self, component: str, key: str, cfg: dict) -> None:
        cfg = {"unique_id": f"{self.uid}_{key}", "device": self.device, **cfg}
        self._pub(f"{self.disc}/{component}/{self.uid}/{key}/config", cfg, retain=True, qos=1)
        self._announced.add(f"{component}.{key}")

    def _announce_all(self) -> None:
        self._pub(f"{self.base}/bridge", "online", retain=True, qos=1)
        self.publish_machine(self._machine_online)
        st = f"{self.base}/state"
        self._config("sensor", "state", {
            "name": "State", "icon": "mdi:coffee-maker", "state_topic": st,
            "value_template": "{{ value_json.state }}",
            "device_class": "enum", "options": list(STATES),
            **self._availability(machine=False),
        })
        for key in self.seen:
            self._announce_sensor(key)
        for key, (name, dc, icon) in BINARY_SENSORS.items():
            c = {"name": name, "state_topic": st,
                 "value_template": "{{ 'ON' if value_json.get('%s') else 'OFF' }}" % key,
                 **self._availability()}
            if dc:
                c["device_class"] = dc
            if icon:
                c["icon"] = icon
            if key in DIAGNOSTIC_BINARY:
                c["entity_category"] = "diagnostic"
            self._config("binary_sensor", key, c)
        for key, (name, icon) in BUTTONS.items():
            self._config("button", key, {
                "name": name, "icon": icon, "command_topic": f"{self.base}/command/{key}",
                "payload_press": "PRESS", **self._availability()})
        ls = f"{self.base}/last_shot"
        self._config("sensor", "last_shot", {
            "name": "Last shot", "icon": "mdi:coffee", "state_topic": ls,
            "device_class": "timestamp", "value_template": "{{ value_json.start }}",
            "json_attributes_topic": ls, **self._availability(machine=False)})
        self._config("sensor", "last_shot_yield", {
            "name": "Last shot yield", "state_topic": ls, "unit_of_measurement": "g",
            "device_class": "weight", "suggested_display_precision": 1,
            "value_template": "{{ value_json.get('yield') }}", **self._availability(machine=False)})
        self._config("sensor", "last_shot_duration", {
            "name": "Last shot duration", "state_topic": ls, "unit_of_measurement": "s",
            "device_class": "duration", "suggested_display_precision": 0,
            "value_template": "{{ value_json.duration }}", **self._availability(machine=False)})
        self._config("sensor", "pending", {
            "name": "Shots waiting for delivery", "icon": "mdi:tray-full",
            "state_topic": f"{self.base}/pending", "state_class": "measurement",
            "entity_category": "diagnostic", **self._availability(machine=False)})
        self.publish_pending(self._pending)
        self._announce_settings()
        # give retained settings from the broker time to arrive (restore) first
        self.loop.call_later(2.0, self._publish_settings)

    def _publish_settings(self) -> None:
        for key, value in self.settings.items():
            self.publish_setting(key, value)

    def _announce_sensor(self, key: str) -> None:
        name, unit, dc, sc, icon, cat, enabled, prec = SENSORS[key]
        c: dict = {"name": name, "state_topic": f"{self.base}/state",
                   "value_template": "{{ value_json.get('%s') }}" % key, **self._availability()}
        for k, v in (("unit_of_measurement", unit), ("device_class", dc), ("state_class", sc),
                     ("icon", icon), ("entity_category", cat),
                     ("suggested_display_precision", prec)):
            if v is not None:
                c[k] = v
        if not enabled:
            c["enabled_by_default"] = False
        if key == "heater_avg_spread":
            c["json_attributes_topic"] = f"{self.base}/state"
            c["json_attributes_template"] = "{{ {'heater_averages': value_json.heater_avgs} | tojson }}"
        self._config("sensor", key, c)

    def _announce_settings(self) -> None:
        s = f"{self.base}/settings"
        self._config("number", "dose", {
            "name": "Dose", "icon": "mdi:scale", "unit_of_measurement": "g",
            "min": 1, "max": 40, "step": 0.1, "mode": "box", "entity_category": "config",
            "state_topic": f"{s}/dose", "command_topic": f"{s}/dose/set",
            **self._availability(machine=False)})
        for key, name, icon in (("bean", "Bean", "mdi:seed"),
                                ("grinder_setting", "Grinder setting", "mdi:cog")):
            self._config("text", key, {
                "name": name, "icon": icon, "max": 100, "entity_category": "config",
                "state_topic": f"{s}/{key}", "command_topic": f"{s}/{key}/set",
                **self._availability(machine=False)})
        if self.cfg.visualizer_enabled:
            self._config("button", "refresh_coffee_bags", {
                "name": "Reload coffee bags", "icon": "mdi:refresh", "entity_category": "config",
                "command_topic": f"{self.base}/command/refresh_coffee_bags",
                "payload_press": "PRESS", **self._availability(machine=False)})
            self._announce_bags()
        switches = []
        if self.cfg.visualizer_enabled:
            switches.append(("visualizer_upload", "Upload shots to Visualizer", "mdi:cloud-upload"))
        if self.cfg.telegram_enabled:
            switches.append(("telegram_notify", "Telegram shot message", "mdi:send"))
        for key, name, icon in switches:
            self._config("switch", key, {
                "name": name, "icon": icon, "entity_category": "config",
                "state_topic": f"{s}/{key}", "command_topic": f"{s}/{key}/set",
                "payload_on": "ON", "payload_off": "OFF", **self._availability(machine=False)})

    def _announce_bags(self) -> None:
        key = "coffee_bag"
        if self._bag_options is None:
            if self._bag_announced:   # remove the entity (e.g. coffee management off)
                self._pub(f"{self.disc}/select/{self.uid}/{key}/config", "", retain=True, qos=1)
                self._bag_announced = False
            return
        s = f"{self.base}/settings"
        self._config("select", key, {
            "name": "Coffee bag", "icon": "mdi:sack", "entity_category": "config",
            "options": self._bag_options,
            "state_topic": f"{s}/{key}", "command_topic": f"{s}/{key}/set",
            "value_template": "{{ value if value else '%s' }}" % NO_BAG,
            **self._availability(machine=False)})
        self._bag_announced = True


def parse_setting(key: str, payload: str) -> Optional[Any]:
    """Convert a settings payload; None when it is not valid."""
    if key == "dose":
        try:
            v = float(payload.replace(",", "."))
        except ValueError:
            return None
        return v if 0 < v <= 100 else None
    if key in ("bean", "grinder_setting"):
        return payload.strip()[:100]
    if key == "coffee_bag":
        p = payload.strip()[:255]
        return "" if p == NO_BAG else p
    if key in ("visualizer_upload", "telegram_notify"):
        p = payload.strip().upper()
        return True if p in ("ON", "TRUE", "1") else False if p in ("OFF", "FALSE", "0") else None
    return None
