"""Configuration: config.yaml, overridden by environment variables.

Every key `<section>.<key>` can be set as the environment variable
`<SECTION>_<KEY>` (e.g. `machine.host` -> `MACHINE_HOST`). `<VAR>_FILE` reads the
value from a file (Docker secrets). The file is LEVA2MQTT_CONFIG or /data/config.yaml.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field, fields
from typing import Any, Optional

DEFAULT_CONFIG_PATH = "/data/config.yaml"


@dataclass
class MachineConfig:
    host: str = ""
    port: int = 23
    name: str = "Espresso machine"
    id: str = ""                    # default: derived from name


@dataclass
class MqttConfig:
    host: str = ""                  # empty = MQTT disabled
    port: int = 1883
    username: str = ""
    password: str = ""
    tls: bool = False
    topic_prefix: str = "leva2mqtt"
    discovery_prefix: str = "homeassistant"
    publish_interval: float = 1.0   # s between live updates while idle (every frame while brewing)


@dataclass
class VisualizerConfig:
    username: str = ""              # empty = upload disabled
    password: str = ""
    upload: bool = True             # default for the runtime switch
    coffee_bags_refresh: float = 1800.0  # s between reloads of the coffee bag list; 0 = off


@dataclass
class TelegramConfig:
    token: str = ""                 # empty = notifications disabled
    chat_id: str = ""
    notify: bool = True             # default for the runtime switch
    language: str = "en"            # message language: en, de


@dataclass
class ShotsConfig:
    directory: str = "/data/shots"  # queue (pending/) and archive; empty = memory only
    keep: bool = False              # keep a shot file after a successful Visualizer upload
    dose: float = 18.0              # default for the runtime dose entity, g
    tail: float = 5.0               # s after the pump stops that still count (drip)
    grinders: list = field(default_factory=list)   # grinder names for the HA select; empty = none


@dataclass
class BridgeConfig:
    report_min_offline: float = 10.0
    ready_band: float = 0.5         # K around the setpoint for "at temperature" / ready
    ready_spread: float = 1.0       # %-points spread of the last three per-minute heater averages
    ready_grace: float = 120.0      # s after a shot / hot water in which a boiler drop keeps ready
    steam_threshold: float = 100.0
    alert_interval: float = 300.0       # s between alert polls (MC@ for ~1 s) while idle
    other_client_quiet: float = 180.0   # s without activity of another client before MCr again


@dataclass
class CaptureConfig:
    enabled: bool = False           # record the raw telnet stream (debugging, protocol work)
    directory: str = "/data/captures"   # one file per day, earlier days gzip-compressed
    keep_days: int = 7              # files older than this are deleted


@dataclass
class Config:
    machine: MachineConfig = field(default_factory=MachineConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    visualizer: VisualizerConfig = field(default_factory=VisualizerConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    shots: ShotsConfig = field(default_factory=ShotsConfig)
    bridge: BridgeConfig = field(default_factory=BridgeConfig)
    capture: CaptureConfig = field(default_factory=CaptureConfig)
    log_level: str = "INFO"

    @property
    def mqtt_enabled(self) -> bool:
        return bool(self.mqtt.host)

    @property
    def visualizer_enabled(self) -> bool:
        return bool(self.visualizer.username and self.visualizer.password)

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram.token and self.telegram.chat_id)


class ConfigError(ValueError):
    pass


def _cast(value: Any, typ: type, name: str) -> Any:
    if value is None:
        return None
    try:
        if typ is list:
            items = value.split(",") if isinstance(value, str) else value
            if not isinstance(items, (list, tuple)):
                raise ValueError(value)
            return [str(x).strip()[:100] for x in items if str(x).strip()]
        if typ is bool:
            if isinstance(value, bool):
                return value
            s = str(value).strip().lower()
            if s in ("1", "true", "yes", "on"):
                return True
            if s in ("0", "false", "no", "off", ""):
                return False
            raise ValueError(value)
        return typ(value) if not isinstance(value, typ) else value
    except (TypeError, ValueError):
        raise ConfigError(f"{name}: cannot read {value!r} as {typ.__name__}") from None


def _env(name: str, environ) -> Optional[str]:
    if name in environ:
        return environ[name]
    path = environ.get(name + "_FILE")
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    return None


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "machine"


def load(path: Optional[str] = None, environ=None) -> Config:
    environ = os.environ if environ is None else environ
    path = path or environ.get("LEVA2MQTT_CONFIG") or DEFAULT_CONFIG_PATH
    data: dict = {}
    if os.path.exists(path):
        import yaml
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            raise ConfigError(f"{path}: expected a mapping at the top level")

    cfg = Config()
    for sec in fields(Config):
        if sec.name == "log_level":
            continue
        section = getattr(cfg, sec.name)
        values = data.get(sec.name) or {}
        if not isinstance(values, dict):
            raise ConfigError(f"{path}: section '{sec.name}' must be a mapping")
        unknown = set(values) - {f.name for f in fields(section)}
        if unknown:
            raise ConfigError(f"{path}: unknown keys in '{sec.name}': {', '.join(sorted(unknown))}")
        for f in fields(section):
            typ = type(getattr(section, f.name))
            name = f"{sec.name}.{f.name}"
            raw = _env(f"{sec.name}_{f.name}".upper(), environ)
            if raw is None and f.name in values:
                raw = values[f.name]
            if raw is not None:
                setattr(section, f.name, _cast(raw, typ, name))
    level = _env("LOG_LEVEL", environ) or data.get("log_level")
    if level:
        cfg.log_level = str(level).upper()

    if not cfg.machine.host:
        raise ConfigError("machine.host (MACHINE_HOST) is required")
    cfg.telegram.language = cfg.telegram.language.strip().lower() or "en"
    if cfg.telegram.language not in ("en", "de"):
        raise ConfigError(f"telegram.language: {cfg.telegram.language!r} is not one of en, de")
    cfg.machine.id = slug(cfg.machine.id or cfg.machine.name)
    if not cfg.mqtt_enabled and not cfg.visualizer_enabled and not cfg.telegram_enabled \
            and not cfg.shots.directory:
        raise ConfigError("nothing to do: configure at least one of mqtt, visualizer, "
                          "telegram or shots.directory")
    return cfg
