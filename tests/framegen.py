"""Builds synthetic MCr frames for tests (positions as in docs/protocol.md)."""

LEN = 289


def _put(buf, pos, text):
    buf[pos:pos + len(text)] = list(text)


def _sealed(buf):
    """Fill in the `~ XX` checksum (two's complement of the 8-bit sum before `~`)."""
    text = "".join(buf)
    i = text.rindex("~")
    cs = (-sum(text[:i].encode("latin-1"))) & 0xFF
    return text[:i] + f"~ {cs:02X}" + text[i + 4:]


def frame(pump="-", mode="PID", temp=90.0, setpoint=90.0, heater=10, volume=0, pulses=0,
          rate=0, target=None, pressure=0.5, weight=None, flow_weight=None,
          profile="GEN.LEVER", standby=False, heater_avg=10.0):
    buf = ["?"] * LEN
    buf[0], buf[1], buf[-1] = "{", " ", "}"
    _put(buf, LEN - 5, "~ 00")
    if standby:
        buf[3] = "1"
        _put(buf, 163, "001.0")
        return _sealed(buf)
    buf[2] = pump
    buf[3] = "1"
    _put(buf, 4, mode)
    _put(buf, 7, f"{temp:06.2f}")
    _put(buf, 13, f"{setpoint:05.1f}")
    _put(buf, 18, f"{heater:03d}")
    if heater_avg is not None:
        _put(buf, 73, f"{heater_avg:05.1f}")
    _put(buf, 152, f"{volume:03d}{pulses:05d}{rate:03d}001.0")
    if target is not None:
        _put(buf, 192, f"{target:05.2f}")
    if pressure is not None:
        _put(buf, 197, f"{pressure:05.2f}")
    if weight is not None:
        _put(buf, 226, f"{weight:05.1f}")
    if flow_weight is not None:
        _put(buf, 231, f"{flow_weight:05.2f}")
    _put(buf, 245, profile.ljust(21))
    return _sealed(buf)
