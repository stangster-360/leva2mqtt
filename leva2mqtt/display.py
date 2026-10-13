"""Parser for the display frames the module streams after `MC@` (docs/protocol.md).

A display frame is `<CCC` + the 4 × 16 characters of the LCD + `>`:

    <102GEN.LEVER       1: 86.6ß \x04 90.0ß\x03             9% 3.3b-  0s   0ml>
        |-- line 1 ----||-- line 2 ----||-- line 3 ----||-- line 4 ----|

CCC: 1st digit display on/off, 2nd input event (0 none, 1/2 encoder turned, 3 click,
9 brew switch / pump start and stop), 3rd screen (2 main / shot screen, 1 menu). Control characters 0x01-0x05 are bar-graph
and arrow glyphs, `ß` (0xDF) is the degree sign.

Main screen, as seen: line 1 profile, line 2 `1: <temp>ß <setpoint>ß` (`ECO` instead of
the setpoint in eco, `READY` for 10 s when ready), line 3 heater bar and output in %,
line 4 pressure, last shot time and weight (g) or volume (ml). Standby shows only
`STANDBY`. Alerts (`BACKFL`, `DSCALE`, `FILTER`, `REFILL`, `PROTECT`, `VALVE`,
`ERR:…`) appear between setpoint and heater output per the manual; no alert has been
captured yet, so where exactly they sit is unverified - they are searched in the whole
main-screen text.

Shot screen, as seen (capture 2026-10-13 `shotscreen`, a flush):

    <102 0s 10.9g       1: 89.8ß  90.0ß  7%    0s R=7.5 0.5   0ß  10.9g>

line 1 shot time and weight, line 2 as on the main screen, line 3 heater output, a
second time and `R=` flow by weight (g/s), line 4 measured pressure (no unit), a target
(`9.0b` at the start, later `0ß`) and the weight. It is recognised by a time in seconds
at the start of line 1.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

ALERTS = ("BACKFL", "DSCALE", "FILTER", "REFILL", "PROTECT", "VALVE")
_ALERT_RE = re.compile(r"(?<![A-Za-z])(BACKFL|DSCALE|FILTER|REFILL|PROTECT|VALVE|ERR:\S*)")
_NUM = r"(-?\d+(?:\.\d+)?)"
_SHOT_RE = re.compile(r"^\s*\d+s\b")


@dataclass(frozen=True)
class Display:
    raw: str
    code: str
    lines: tuple[str, str, str, str]   # control characters replaced by spaces
    display_on: bool
    input_event: bool                  # encoder turned or clicked in this frame
    main: bool                         # main or shot screen (not a menu)
    standby: bool = False
    eco: bool = False
    ready: bool = False                # firmware READY shown (10 s)
    shot: bool = False                 # shot screen (unverified)
    profile: Optional[str] = None
    temperature: Optional[float] = None
    setpoint: Optional[float] = None
    heater: Optional[int] = None
    pressure: Optional[float] = None
    weight: Optional[float] = None
    volume: Optional[int] = None
    alerts: tuple[str, ...] = ()


def _f(pattern: str, text: str) -> Optional[float]:
    m = re.search(pattern, text)
    return float(m.group(1)) if m else None


def parse_display(line: str) -> Optional[Display]:
    s = line.rstrip("\r\n")
    if not (s.startswith("<") and s.endswith(">")) or len(s) < 5 or not s[1:4].isdigit():
        return None
    code, body = s[1:4], s[4:-1]
    clean = re.sub(r"[\x00-\x1f]", " ", body).ljust(64)[:64]
    lines = (clean[0:16], clean[16:32], clean[32:48], clean[48:64])
    base = dict(raw=s, code=code, lines=lines, display_on=code[0] == "1",
                input_event=code[1] != "0", main=code[2] == "2")
    if not base["main"]:
        return Display(**base)
    if "STANDBY" in clean:
        return Display(**base, standby=True)

    l1, l2, l3, l4 = lines
    temps = [float(x) for x in re.findall(r"(-?\d+(?:\.\d+)?)\s*ß", l2)]
    weight = _f(_NUM + r"g\b", l4)
    volume = _f(r"(\d+)ml\b", l4)
    shot = bool(_SHOT_RE.match(l1))
    return Display(
        **base,
        eco="ECO" in l2,
        ready="READY" in l2,
        shot=shot,
        profile=None if shot or "ß" in l1 else (l1.strip() or None),
        temperature=temps[0] if temps else None,
        setpoint=temps[1] if len(temps) > 1 else None,
        heater=int(h) if (h := _f(r"(\d{1,3})%", l3)) is not None else None,
        pressure=_f(r"^\s*" + _NUM, l4) if shot else _f(_NUM + r"b", l4),
        weight=weight,
        volume=int(volume) if volume is not None else None,
        alerts=tuple(dict.fromkeys(_ALERT_RE.findall(clean))),
    )
