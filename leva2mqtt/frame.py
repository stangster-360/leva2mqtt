"""Parser for the rich sensor frames the module streams after `MCr`.

Positions follow the Status Monitor definition for leva! 3.0 (`leva!-64EA1111.xml`, the
name the module reports in its `MC?` reply) and match our own captures, see
docs/protocol.md. A frame is a fixed-width line `{ … }` of 289 characters. Undefined
values (sensor missing, machine in standby) are `?`; such fields become None instead of
raising.
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
    pump: Optional[bool] = None            # pos 2: '*' pump / brew program active, '-' idle
    pid_selected: Optional[int] = None     # pos 3: always 1 here; perhaps the PID chosen with MC1/MC2 (unverified)
    mode: Optional[str] = None             # pos 4-6: PID, ECO, OFF, CLD, ATN; None in standby
    temperature: Optional[float] = None    # pos 7-12: boiler, °C
    setpoint: Optional[float] = None       # pos 13-17: °C, one decimal (eco setpoint while in eco)
    heater: Optional[int] = None           # pos 18-20: heater output, %, '100' at full power
    pid_p: Optional[float] = None          # pos 21-26
    pid_i: Optional[float] = None          # pos 27-32
    pid_d: Optional[float] = None          # pos 33-38
    pid_ff: Optional[float] = None         # pos 39-44: feed-forward term
    temperature_rate: Optional[float] = None  # pos 45-52: dPV/dt, used by the D term
    pid_kc: Optional[float] = None         # pos 53-57: Kc, %/°C
    pid_ti: Optional[float] = None         # pos 58-62: Ti, s
    pid_td: Optional[float] = None         # pos 63-67: Td, s
    pid_windup: Optional[float] = None     # pos 68-72: wind-up guard, %
    heater_avg: Optional[float] = None     # pos 73-77: average heater output over the last minute, %
    mode2: Optional[str] = None            # pos 78-80: PID 2 control type (OFF here)
    temperature2: Optional[float] = None   # pos 81-85: PID 2 temperature, °C
    setpoint2: Optional[float] = None      # pos 87-91: PID 2 setpoint, °C
    heater2: Optional[int] = None          # pos 92-94: PID 2 output, %
    pid2_p: Optional[float] = None         # pos 95-100
    pid2_i: Optional[float] = None         # pos 101-106
    pid2_d: Optional[float] = None         # pos 107-112
    pid2_ff: Optional[float] = None        # pos 113-118
    temperature2_rate: Optional[float] = None  # pos 119-125: PID 2 dPV/dt
    pid2_kc: Optional[float] = None        # pos 127-131
    pid2_ti: Optional[float] = None        # pos 132-136
    pid2_td: Optional[float] = None        # pos 137-141
    pid2_windup: Optional[float] = None    # pos 142-146
    heater2_avg: Optional[float] = None    # pos 147-151
    volume: Optional[int] = None           # pos 152-154: flow meter volume since pump start, ml
    pulses: Optional[int] = None           # pos 155-159: flow meter ticks
    flow_rate: Optional[int] = None        # pos 160-162: flow meter rate, ml/min (XML: ×0.01666 = ml/s)
    increment: Optional[float] = None      # pos 163-167: setpoint increment for MC+/MC-
    temperature_pcb: Optional[float] = None   # pos 168-172: PCB sensor, °C
    temperature_aux1: Optional[float] = None  # pos 174-178: AUX 1 sensor, °C
    temperature_aux2: Optional[float] = None  # pos 180-184: AUX 2 sensor, °C
    temperature_aux3: Optional[float] = None  # pos 186-190: AUX 3 sensor, °C
    target_pressure: Optional[float] = None  # pos 192-196: pressure setpoint, bar
    pressure: Optional[float] = None       # pos 197-201: brew pressure, bar
    pressure_min: Optional[float] = None   # pos 202-206: pressure band (min), bar
    pressure_max: Optional[float] = None   # pos 207-211: pressure band (max), bar
    pump_phase: Optional[int] = None       # pos 212-214: pump phase angle, °
    pump_phase_bias: Optional[int] = None  # pos 215-217: bias term of the pump phase, °
    pump_phase_p: Optional[int] = None     # pos 218-221: P term of the pump phase, °
    pump_phase_i: Optional[int] = None     # pos 222-225: I term of the pump phase, °
    weight: Optional[float] = None         # pos 226-230: scale, g
    flow_weight: Optional[float] = None    # pos 231-235: weight change, g/s
    profile1: Optional[str] = None         # pos 236-244: profile slot 1 (9 characters)
    profile2: Optional[str] = None         # pos 245-253: profile slot 2
    profile3: Optional[str] = None         # pos 254-262: profile slot 3
    profile4: Optional[str] = None         # pos 263-271: profile slot 4
    profile: Optional[str] = None          # pos 272-280: selected profile
    flow_target: Optional[int] = None      # pos 281-283: flow tracking target, ml/min (XML: ×0.01666 = ml/s)

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


def checksum_ok(f: str) -> bool:
    """`~ XX` before `}` is the two's complement of the 8-bit sum of all characters before
    `~` (ito manual, "Creating compatible firmware"; matched 21 172 of 21 173 captured
    frames, the one miss was a line with lost characters). Frames without `~` pass."""
    i = f.rfind("~")
    if i < 0:
        return True
    try:
        want = int(f[i + 1:].strip(" }"), 16)
    except ValueError:
        return False
    return (-sum(f[:i].encode("latin-1", "replace"))) & 0xFF == want


def parse(line: str) -> Optional[Frame]:
    """Decode one `{…}` line. Returns None if it is not an `MCr` frame."""
    f = line.rstrip("\r\n")
    if not (f.startswith("{") and f.endswith("}")):
        return None
    if not checksum_ok(f):
        log.debug("MCr frame with bad checksum dropped")
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
        pid_selected=_num(f[3:4], int),
        mode=mode,
        temperature=_num(f[7:13]),
        setpoint=_num(f[13:18]),
        heater=_num(f[18:21], int),
        pid_p=_num(f[21:27]),
        pid_i=_num(f[27:33]),
        pid_d=_num(f[33:39]),
        pid_ff=_num(f[39:45]),
        temperature_rate=_num(f[45:53]),
        pid_kc=_num(f[53:58]),
        pid_ti=_num(f[58:63]),
        pid_td=_num(f[63:68]),
        pid_windup=_num(f[68:73]),
        heater_avg=_num(f[73:78]),
        mode2=_text(f[78:81]),
        temperature2=_num(f[81:86]),
        setpoint2=_num(f[87:92]),
        heater2=_num(f[92:95], int),
        pid2_p=_num(f[95:101]),
        pid2_i=_num(f[101:107]),
        pid2_d=_num(f[107:113]),
        pid2_ff=_num(f[113:119]),
        temperature2_rate=_num(f[119:126]),
        pid2_kc=_num(f[127:132]),
        pid2_ti=_num(f[132:137]),
        pid2_td=_num(f[137:142]),
        pid2_windup=_num(f[142:147]),
        heater2_avg=_num(f[147:152]),
        volume=_num(f[152:155], int),
        pulses=_num(f[155:160], int),
        flow_rate=_num(f[160:163], int),
        increment=_num(f[163:168]),
        temperature_pcb=_num(f[168:173]),
        temperature_aux1=_num(f[174:179]),
        temperature_aux2=_num(f[180:185]),
        temperature_aux3=_num(f[186:191]),
        target_pressure=_num(f[192:197]),
        pressure=_num(f[197:202]),
        pressure_min=_num(f[202:207]),
        pressure_max=_num(f[207:212]),
        pump_phase=_num(f[212:215], int),
        pump_phase_bias=_num(f[215:218], int),
        pump_phase_p=_num(f[218:222], int),
        pump_phase_i=_num(f[222:226], int),
        weight=_num(f[226:231]),
        flow_weight=_num(f[231:236]),
        profile1=_text(f[236:245]),
        profile2=_text(f[245:254]),
        profile3=_text(f[254:263]),
        profile4=_text(f[263:272]),
        profile=_text(f[272:281]),
        flow_target=_num(f[281:284], int),
    )
