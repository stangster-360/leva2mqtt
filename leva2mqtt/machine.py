"""Machine state derived from the `MCr` stream (docs/detection-rules.md)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .frame import Frame

# Overall states published to Home Assistant
OFFLINE = "offline"
STANDBY = "standby"
ECO = "eco"
HEATING = "heating"
IDLE = "idle"          # awake, near the setpoint, not (yet) ready
READY = "ready"        # derived by the bridge like the firmware's ready beep (unverified)
BREWING = "brewing"    # pump / brew program running ('*')
HOT_WATER = "hot_water"
STEAM = "steam"
STATES = (OFFLINE, STANDBY, ECO, HEATING, IDLE, READY, BREWING, HOT_WATER, STEAM)

# Power modes from pos 4-6
MODE_STANDBY, MODE_ECO, MODE_AWAKE = "standby", "eco", "awake"


def power_mode(frame: Frame) -> str:
    if frame.standby:
        return MODE_STANDBY
    if frame.eco:
        return MODE_ECO
    return MODE_AWAKE


@dataclass
class Settings:
    """Thresholds; the defaults rest on single captures and are unverified."""

    heating_margin: float = 1.5     # K below setpoint = heating (seen)
    at_temp_band: float = 0.5       # K around the setpoint = boiler at temperature
    ready_spread: float = 1.0       # %-points: last three per-minute heater averages (2 hits)
    ready_samples: int = 3
    avg_period: float = 60.0        # s between updates of the per-minute average (seen)
    steam_on: float = 100.0         # °C boiler with setpoint < steam_on = steam mode (threshold unverified)
    steam_off: float = 99.0         # °C: steam mode left (unverified)
    hot_water_min_ml: int = 5       # flow meter increase without '*' (one capture)
    hot_water_idle: float = 3.0     # s without increase ends hot water
    pump_settle: float = 3.0        # s after '*' ends before counter changes count as hot water


class Tracker:
    """Turns frames into a state. Call update() for every frame, link_down() on loss."""

    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or Settings()
        self.mode: Optional[str] = None
        self.state = OFFLINE
        self.ready = False
        self.steam = False
        self.hot_water = False
        self.at_temperature = False
        self._avgs: list[float] = []          # last per-minute heater averages, oldest first
        self._avg_t: Optional[float] = None   # time of the last update of the field
        self._pump_end: Optional[float] = None
        self._pump_on = False
        self._vol_base: Optional[int] = None
        self._vol_last: Optional[int] = None
        self._vol_change: float = 0.0

    def link_down(self) -> None:
        self.mode = None
        self.state = OFFLINE
        self.ready = self.steam = self.hot_water = self.at_temperature = False
        self._reset_ready()
        self._vol_base = self._vol_last = None
        self._pump_on = False

    def update(self, f: Frame, t: float) -> Optional[tuple[str, str]]:
        """Process one frame. Returns (old, new) power mode on a mode change."""
        old_mode = self.mode
        self.mode = power_mode(f)
        change = (old_mode, self.mode) if old_mode != self.mode else None

        if self.mode != MODE_AWAKE:
            self.ready = self.steam = self.hot_water = self.at_temperature = False
            self._reset_ready()
            self._vol_base = self._vol_last = None
            self._pump_on = False
            self.state = STANDBY if self.mode == MODE_STANDBY else ECO
            return change

        self._update_pump(f, t)
        self._update_steam(f)
        self._update_hot_water(f, t)
        self._update_ready(f, t)

        temp, sp = f.temperature, f.setpoint
        if self._pump_on:
            self.state = BREWING
        elif self.steam:
            self.state = STEAM
        elif self.hot_water:
            self.state = HOT_WATER
        elif self.ready:
            self.state = READY
        elif temp is not None and sp is not None and temp < sp - self.s.heating_margin:
            self.state = HEATING
        else:
            self.state = IDLE
        return change

    @property
    def heating(self) -> bool:
        return self.state == HEATING

    def _update_pump(self, f: Frame, t: float) -> None:
        on = bool(f.pump)
        if self._pump_on and not on:
            self._pump_end = t
        if on and not self._pump_on:
            self.hot_water = False
        self._pump_on = on

    def _update_steam(self, f: Frame) -> None:
        temp, sp = f.temperature, f.setpoint
        if temp is None:
            return
        if not self.steam and temp >= self.s.steam_on and (sp is None or sp < self.s.steam_on):
            self.steam = True
        elif self.steam and temp < self.s.steam_off:
            self.steam = False

    def _update_hot_water(self, f: Frame, t: float) -> None:
        # The flow meter counter is reset when '*' starts and keeps counting while
        # hot water is drawn without '*' (one capture).
        vol = f.volume
        if vol is None or self._pump_on:
            self._vol_base = self._vol_last = None
            self.hot_water = False
            return
        if self._pump_end is not None and t - self._pump_end < self.s.pump_settle:
            self._vol_base = self._vol_last = vol   # flow meter still running down
            return
        if self._vol_base is None or vol < self._vol_base:
            self._vol_base = self._vol_last = vol
            self._vol_change = t
            return
        if vol != self._vol_last:
            self._vol_last = vol
            self._vol_change = t
        if not self.hot_water and vol - self._vol_base >= self.s.hot_water_min_ml:
            self.hot_water = True
        elif self.hot_water and t - self._vol_change >= self.s.hot_water_idle:
            self.hot_water = False
            self._vol_base = vol

    def _reset_ready(self) -> None:
        self.ready = False
        self._avgs = []
        self._avg_t = None

    def _avg_updated(self, f: Frame, t: float) -> bool:
        """Track the per-minute heater average (pos 73-77). Returns True on an update.

        The field changes once a minute (seen). An update that repeats the old value is
        not visible in the frame, so an unchanged value is counted again once a full
        period plus a margin has passed (unverified)."""
        v = f.heater_avg
        if v is None:
            return False
        if self._avg_t is None or not self._avgs or v != self._avgs[-1]:
            self._avg_t = t
        elif t - self._avg_t >= self.s.avg_period + 5:
            self._avg_t += self.s.avg_period
        else:
            return False
        self._avgs = (self._avgs + [v])[-self.s.ready_samples:]
        return True

    def _update_ready(self, f: Frame, t: float) -> None:
        """Ready like the firmware's ready beep (docs/detection-rules.md, section 3):
        boiler within ±0.5 K of the setpoint and the last three per-minute heater
        averages within less than 1 percentage point, evaluated at each update of
        the average. Two hits, unverified."""
        temp, sp = f.temperature, f.setpoint
        updated = self._avg_updated(f, t)
        if temp is None or sp is None:
            self.at_temperature = False
            return
        self.at_temperature = abs(temp - sp) <= self.s.at_temp_band
        if self.steam:
            self._reset_ready()
            return
        if self.ready:
            # lost when the boiler falls too far outside a shot (eco, standby and
            # steam reset it elsewhere)
            if not self._pump_on and temp < sp - self.s.heating_margin:
                self.ready = False
            return
        if (updated and self.at_temperature and len(self._avgs) == self.s.ready_samples
                and max(self._avgs) - min(self._avgs) < self.s.ready_spread):
            self.ready = True


class WakeReporter:
    """Decides when the bridge sends `MCcREPORT SP1` (then `MCr`).

    Requirement: when the machine becomes reachable or wakes from standby/eco, the
    bridge sends `MCcREPORT SP1` and then `MCr`. A restart of the bridge must not
    cause a beep.

    `MCcREPORT SP1` wakes the machine from standby and eco (seen), so it is only sent
    while the machine is awake. A pending report waits until it is.

    - first connection after a bridge start: no report
    - reachable again after the bridge saw it offline for at least `min_offline`
      seconds: report. Any drop takes at least 5 s (3 s watchdog + 2 s retry);
      a power cycle takes longer (module up 4-20 s after power-on, first MCr often
      lost). Default 10 s; threshold unverified
    - power mode standby/eco -> awake: report
    - a report the user triggered through the bridge suppresses the automatic one
      that would follow its own wake-up
    """

    SUPPRESS_S = 5.0

    def __init__(self, min_offline: float = 10.0):
        self.min_offline = min_offline
        self.pending = False
        self._suppress_until = 0.0

    def link_up(self, t: float, offline_since: Optional[float]) -> None:
        if offline_since is not None and t - offline_since >= self.min_offline:
            self.pending = True

    def mode_change(self, old: Optional[str], new: str, t: float) -> None:
        if old in (MODE_STANDBY, MODE_ECO) and new == MODE_AWAKE and t >= self._suppress_until:
            self.pending = True

    def manual_report(self, t: float) -> None:
        self.pending = False
        self._suppress_until = t + self.SUPPRESS_S

    def due(self, mode: Optional[str]) -> bool:
        """True once when the report should be sent now."""
        if self.pending and mode == MODE_AWAKE:
            self.pending = False
            return True
        return False
