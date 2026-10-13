"""Parser for the rich sensor frames the module streams after `MCr`.

Field positions come from our own captures, see docs/protocol.md. A frame is a
fixed-width line `{ … }` of 289 characters. Undefined values (sensor missing,
machine in standby) are `?`; such fields become None instead of raising.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, fields
from typing import Optional

log = logging.getLogger(__name__)

FRAME_LEN = 289
_NUM = re.compile(r"^\s*-?\d+(\.\d+)?\s*$")


@dataclass(frozen=True)
class Frame:
    """One decoded `MCr` frame. Every sensor field is optional."""

    raw: str
    pump: Optional[bool]            # pos 2: '*' pump / brew program active, '-' idle
    mode: Optional[str]             # pos 4-6: PID, ECO, OFF, CLD, ATN; None in standby
    temperature: Optional[float]    # pos 7-12: boiler, °C
    setpoint: Optional[float]       # pos 13-17: °C, one decimal (eco setpoint while in eco)
    heater: Optional[int]           # pos 18-20: heater output, %, '100' at full power
    pid_p: Optional[float]          # pos 21-26
    pid_i: Optional[float]          # pos 27-32
    pid_d: Optional[float]          # pos 33-38
    temperature_rate: Optional[float]  # pos 45-52: rate of change used by the D term
    pid_kc: Optional[float]         # pos 53-57: PID constant Kc
    pid_ti: Optional[float]         # pos 58-62: PID constant Ti
    pid_td: Optional[float]         # pos 63-67: PID constant Td
    heater_avg: Optional[float]     # pos 73-77: average heater output over the last minute, %
    volume: Optional[int]           # pos 152-154: flow meter volume since pump start, ml
    pulses: Optional[int]           # pos 155-159: flow meter pulses
    flow_rate: Optional[int]        # pos 160-162: flow meter rate, ml/min
    target_pressure: Optional[float]  # pos 192-196: target of the active profile, bar
    pressure: Optional[float]       # pos 197-201: brew pressure, bar (current value: unverified)
    pressure_min: Optional[float]   # pos 202-206: brew pressure, bar (minimum of the interval: unverified)
    pressure_max: Optional[float]   # pos 207-211: brew pressure, bar (maximum of the interval: unverified)
    weight: Optional[float]         # pos 226-230: scale, g
    flow_weight: Optional[float]    # pos 231-235: flow by weight, g/s
    profile: Optional[str]          # pos 245-265: active profile name (position unverified)

    @property
    def standby(self) -> bool:
        return self.mode is None

    @property
    def eco(self) -> bool:
        return self.mode == "ECO"

    @property
    def awake(self) -> bool:
        return self.mode is not None and self.mode != "ECO"


SENSOR_FIELDS = tuple(f.name for f in fields(Frame) if f.name not in ("raw",))


def _num(s: str, cast=float):
    if "?" in s or not _NUM.match(s):
        return None
    try:
        return cast(float(s)) if cast is int else cast(s)
    except ValueError:
        return None


def _text(s: str) -> Optional[str]:
    s = s.strip()
    if not s or "?" in s:
        return None
    return s


_warned_len: set[int] = set()


def parse(line: str) -> Optional[Frame]:
    """Decode one `{…}` line. Returns None if it is not an `MCr` frame."""
    f = line.rstrip("\r\n")
    if not (f.startswith("{") and f.endswith("}")):
        return None
    if len(f) != FRAME_LEN and len(f) not in _warned_len:
        # Other firmware versions may use another layout. Fields are still parsed,
        # and every field that does not look like a number is dropped.
        _warned_len.add(len(f))
        log.warning("MCr frame has %d characters, expected %d – field positions may differ",
                    len(f), FRAME_LEN)
    if len(f) < 80:
        return None

    flag = f[2]
    mode = _text(f[4:7])
    if mode is not None and not mode.isalpha():
        mode = None
    return Frame(
        raw=f,
        pump=True if flag == "*" else False if flag == "-" else None,
        mode=mode,
        temperature=_num(f[7:13]),
        setpoint=_num(f[13:18]),
        heater=_num(f[18:21], int),
        pid_p=_num(f[21:27]),
        pid_i=_num(f[27:33]),
        pid_d=_num(f[33:39]),
        temperature_rate=_num(f[45:53]),
        pid_kc=_num(f[53:58]),
        pid_ti=_num(f[58:63]),
        pid_td=_num(f[63:68]),
        heater_avg=_num(f[73:78]),
        volume=_num(f[152:155], int),
        pulses=_num(f[155:160], int),
        flow_rate=_num(f[160:163], int),
        target_pressure=_num(f[192:197]),
        pressure=_num(f[197:202]),
        pressure_min=_num(f[202:207]),
        pressure_max=_num(f[207:212]),
        weight=_num(f[226:231]),
        flow_weight=_num(f[231:236]),
        profile=_text(f[245:266]),
    )
