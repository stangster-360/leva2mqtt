"""Records pump runs ('*' flag) and classifies them (docs/detection-rules.md, section 4)."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .frame import Frame

log = logging.getLogger(__name__)

SHOT = "shot"
FLUSH = "flush"
TANK_EMPTY = "tank_empty"   # unverified (one old capture)


@dataclass
class Sample:
    t: float                       # seconds since pump start
    pump: bool
    pressure: Optional[float]
    target_pressure: Optional[float]
    flow_rate: Optional[int]       # ml/min
    volume: Optional[int]
    weight: Optional[float]
    flow_weight: Optional[float]
    temperature: Optional[float]
    setpoint: Optional[float]
    pump_phase: Optional[int] = None      # pump phase angle, ° (profile runs)
    flow_target: Optional[int] = None     # flow tracking target, ml/min (leva! 3.0)
    pressure_min: Optional[float] = None  # pressure band of the last interval, bar
    pressure_max: Optional[float] = None


@dataclass
class Run:
    start: float                   # unix time of the pump start
    kind: str = FLUSH
    end: Optional[float] = None    # unix time when '*' ended
    profile: Optional[str] = None
    samples: list[Sample] = field(default_factory=list)

    # filled by finish()
    duration: float = 0.0
    yield_g: Optional[float] = None
    volume_ml: Optional[int] = None
    peak_pressure: Optional[float] = None
    first_drop: Optional[float] = None
    temp_start: Optional[float] = None
    temp_min: Optional[float] = None
    flow_weight_avg: Optional[float] = None

    @property
    def curve(self) -> list[Sample]:
        """Samples up to the pump stop, for the exported shot curve.

        The run keeps recording for `tail` seconds after the pump stopped so the drip is
        included in the yield. Those samples are not part of the curve: the duration
        shown by Visualizer is the length of the curve and must equal `duration` (the
        pump time, as in Telegram and MQTT). After the pump stop the pressure sensor,
        which sits between pump and boiler, reads the boiler sealed off by the three-way
        valve, and the flow meter holds its last rate for a while (docs/protocol.md).
        """
        if not self.duration:
            return list(self.samples)
        return [s for s in self.samples if s.t <= self.duration + 1e-6]

    @property
    def started_at(self) -> datetime:
        return datetime.fromtimestamp(self.start, timezone.utc)

    def summary(self) -> dict:
        def r(v, n=1):
            return None if v is None else round(v, n)
        return {
            "kind": self.kind,
            "start": self.started_at.isoformat(timespec="seconds"),
            "duration": r(self.duration),
            "yield": r(self.yield_g),
            "volume": self.volume_ml,
            "peak_pressure": r(self.peak_pressure),
            "first_drop": r(self.first_drop),
            "flow_weight_avg": r(self.flow_weight_avg, 2),
            "temperature_start": r(self.temp_start, 2),
            "temperature_min": r(self.temp_min, 2),
            "profile": self.profile,
        }


def _max(values):
    values = [v for v in values if v is not None]
    return max(values) if values else None


class ShotRecorder:
    """Feed every frame to update(); it returns a finished Run when one is complete.

    A run starts when '*' is set and ends when it is cleared; `duration` is this pump
    time. The record is closed `tail` seconds later so the drip into the cup is included
    in the yield; the exported curve still ends at the pump stop (`Run.curve`).
    """

    def __init__(self, tail: float = 5.0, shot_pressure: float = 3.0,
                 first_drop_weight: float = 0.2, max_samples: int = 4 * 600):
        self.tail = tail
        self.shot_pressure = shot_pressure            # seen (3 runs)
        self.first_drop_weight = first_drop_weight    # first scale reading above it (13.10.: 0.3 / 0.6 g)
        self.max_samples = max_samples
        self.run: Optional[Run] = None

    def update(self, f: Frame, t: float) -> Optional[Run]:
        done = None
        pump = bool(f.pump)
        if self.run is not None and self.run.end is not None:
            if pump or t - self.run.end >= self.tail:
                done = self._finish()
        if self.run is None and pump:
            self.run = Run(start=t, profile=f.profile)
        if self.run is not None:
            run = self.run
            if run.end is None and not pump:
                run.end = t
            if len(run.samples) < self.max_samples:
                run.samples.append(Sample(
                    t=t - run.start, pump=pump, pressure=f.pressure,
                    target_pressure=f.target_pressure, flow_rate=f.flow_rate,
                    volume=f.volume, weight=f.weight, flow_weight=f.flow_weight,
                    temperature=f.temperature, setpoint=f.setpoint,
                    pump_phase=f.pump_phase, flow_target=f.flow_target,
                    pressure_min=f.pressure_min, pressure_max=f.pressure_max))
            if pump and f.pressure is not None and f.pressure > self.shot_pressure:
                run.kind = SHOT
        return done

    def abort(self) -> Optional[Run]:
        """Close a run on link loss."""
        if self.run is None:
            return None
        if self.run.end is None and self.run.samples:
            self.run.end = self.run.start + self.run.samples[-1].t
        return self._finish()

    def _finish(self) -> Optional[Run]:
        run, self.run = self.run, None
        if run is None or not run.samples:
            return None
        end = run.end if run.end is not None else run.start + run.samples[-1].t
        run.duration = end - run.start
        pumping = [s for s in run.samples if s.pump]
        run.yield_g = _max(s.weight for s in run.samples)
        run.volume_ml = _max(s.volume for s in pumping)
        run.peak_pressure = _max(s.pressure for s in pumping)
        temps = [s.temperature for s in pumping if s.temperature is not None]
        run.temp_start = temps[0] if temps else None
        run.temp_min = min(temps) if temps else None
        for s in run.samples:
            if s.weight is not None and s.weight > self.first_drop_weight:
                run.first_drop = s.t
                break
        if run.yield_g is not None and run.first_drop is not None and run.duration > run.first_drop:
            run.flow_weight_avg = run.yield_g / (run.duration - run.first_drop)
        if run.kind == FLUSH and run.duration >= 5:
            # unverified: target pressure set, no pressure and almost no flow
            rates = [s.flow_rate for s in pumping if s.flow_rate is not None]
            targets = [s.target_pressure for s in pumping if s.target_pressure is not None]
            if (rates and sum(rates) / len(rates) < 100 and targets and max(targets) >= 8
                    and (run.peak_pressure or 0) < self.shot_pressure):
                run.kind = TANK_EMPTY
        log.info("run finished: %s", run.summary())
        return run
