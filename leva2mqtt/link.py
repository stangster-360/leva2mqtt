"""Telnet link to the ito module.

Behaviour derived from captures (docs/protocol.md, docs/detection-rules.md):

- plain TCP on port 23, latin-1, CR+LF line endings
- `MCr` has to be sent after every connect; the report mode is global for the module
  and may have been left in another mode by a previous client
- when the module loses power the TCP connection is not closed: a watchdog drops the
  connection when nothing at all arrives for WATCHDOG_S
- a bare newline every KEEPALIVE_S; after a module reboot the first one is answered
  with a TCP reset, which ends the dead connection
- heartbeat-only state (`$id: leva! …` once per second, no frames): an `MCr` sent too
  early after power-on is lost; re-send it every RESEND_S until frames arrive
- the report mode is global: `MC@` from another client (seen: the Leva Companion app)
  switches every connection to display frames. The link reports this (`XML=` reply,
  setup-dump lines, or display frames while `MCr` is wanted) through `on_foreign`
  instead of switching back, so the app keeps working; the bridge decides when to
  ask for `MCr` again (`hold`)
- the bridge itself switches to display frames for a moment to read alerts
  (`request("display")`, then `request("mcr")`)
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Callable, Optional

from .display import Display, parse_display
from .frame import Frame, parse

log = logging.getLogger(__name__)

PORT = 23
CONNECT_TIMEOUT_S = 3.0
RETRY_S = 2.0
KEEPALIVE_S = 5.0
WATCHDOG_S = 3.0
RESEND_S = 5.0
SWITCH_GRACE_S = 1.5   # frames of the old mode may still arrive after a switch (0.1-0.5 s seen)
_DUMP_RE = re.compile(r"^(\|.*|[A-Z][\w ]*\.\.\.\s*)$")   # setup dump lines (after MCu)

# Only these commands are ever sent. Others change setpoints or PID values
# (MC+, MC-, MCp, MCi, MCd, MCw, …) or toggle reporting (MC<space>).
ALLOWED_COMMANDS = {
    "MCr",            # report sensor data continuously
    "MC@",            # report display (to read alerts)
    "MCa",            # wake from eco / standby
    "MCe",            # enter eco
    "MCE",            # enter standby
    "MCcREPORT SP1",  # beep setpoint 1 (also wakes the machine)
}

FrameHandler = Callable[[Frame, float], None]
DisplayHandler = Callable[[Display, float], None]
LinkHandler = Callable[[bool, float, Optional[float]], None]
ForeignHandler = Callable[[str, float], None]
MODE_COMMANDS = {"mcr": "MCr", "display": "MC@"}


class Link:
    """Keeps one connection to the module and delivers decoded `MCr` frames.

    on_frame(frame, t)                  for every `MCr` frame (t = arrival, unix time)
    on_display(display, t)              for every display frame
    on_foreign(reason, t)               another client is using the module
    on_link(up, t, offline_since)       up=True on the first frame of a connection;
                                        offline_since = when frames were last seen
                                        (or the first failed connect), None if the
                                        bridge has never been offline before
                                        up=False when the connection is lost
    """

    def __init__(self, host: str, port: int, on_frame: FrameHandler, on_link: LinkHandler,
                 on_display: Optional[DisplayHandler] = None,
                 on_foreign: Optional[ForeignHandler] = None,
                 clock: Callable[[], float] = time.time):
        self.host, self.port = host, port
        self.on_frame, self.on_link = on_frame, on_link
        self.on_display, self.on_foreign = on_display, on_foreign
        self.clock = clock
        self.mode = "mcr"          # report mode the bridge wants
        self.hold = False          # another client is active: do not switch back on our own
        self._mode_t = 0.0
        self._last_mcr = 0.0
        self._writer: Optional[asyncio.StreamWriter] = None
        self._up = False
        self._offline_since: Optional[float] = None
        self._last_frame = 0.0
        self._last_rx = 0.0
        self._stop = asyncio.Event()

    @property
    def up(self) -> bool:
        return self._up

    def stop(self) -> None:
        self._stop.set()

    async def send(self, cmd: str) -> bool:
        if cmd not in ALLOWED_COMMANDS:
            raise ValueError(f"command not allowed: {cmd!r}")
        w = self._writer
        if w is None:
            log.warning("not connected, %s not sent", cmd)
            return False
        try:
            w.write(cmd.encode("latin-1") + b"\r\n")
            await w.drain()
            log.info(">> %s", cmd)
            return True
        except OSError as e:
            log.warning("sending %s failed: %s", cmd, e)
            return False

    async def request(self, mode: str) -> bool:
        """Switch the module's (global) report mode: "mcr" or "display"."""
        self.mode = mode
        self._mode_t = self.clock()
        if mode == "mcr":
            self._last_mcr = self._mode_t
        return await self.send(MODE_COMMANDS[mode])

    async def run(self) -> None:
        while not self._stop.is_set():
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT_S)
            except (OSError, asyncio.TimeoutError) as e:
                if self._offline_since is None:
                    self._offline_since = self.clock()
                    log.info("module %s:%s unreachable (%s), retrying every %.0f s",
                             self.host, self.port, e.__class__.__name__, RETRY_S)
                await self._sleep(RETRY_S)
                continue
            log.info("connected to %s:%s", self.host, self.port)
            self._writer = writer
            try:
                await self._session(reader, writer)
            except (OSError, asyncio.IncompleteReadError, ConnectionError) as e:
                log.info("connection lost: %s", e)
            finally:
                self._writer = None
                writer.close()
                try:
                    await asyncio.wait_for(writer.wait_closed(), 1.0)
                except (OSError, asyncio.TimeoutError):
                    pass
                self._set_down()
            if not self._stop.is_set():
                await self._sleep(RETRY_S)

    async def _sleep(self, s: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), s)
        except asyncio.TimeoutError:
            pass

    def _set_down(self) -> None:
        if self._up:
            self._up = False
            self._offline_since = self._last_frame
            self.on_link(False, self.clock(), None)
        elif self._offline_since is None:
            # connected but never got a frame, e.g. a fresh bridge start against a
            # module that is still booting: count as offline from now on
            self._offline_since = self.clock()

    def _came_up(self, now: float) -> None:
        self._last_frame = now
        if not self._up:
            self._up = True
            offline = self._offline_since
            self._offline_since = None
            self.on_link(True, now, offline)

    def _settled(self, now: float) -> bool:
        return now - self._mode_t > SWITCH_GRACE_S

    def _foreign(self, reason: str, now: float) -> None:
        if self.on_foreign:
            self.on_foreign(reason, now)

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        now = self.clock()
        self._last_rx = now
        self._last_frame = now
        last_keepalive = now
        await self.request("mcr")
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = await asyncio.wait_for(reader.read(4096), 0.5)
            except asyncio.TimeoutError:
                chunk = None
            now = self.clock()
            if chunk == b"":
                raise ConnectionError("closed by module")
            if chunk:
                self._last_rx = now
                buf += chunk
                *lines, buf = buf.split(b"\n")
                for raw in lines:
                    text = raw.strip(b"\r").decode("latin-1")
                    if text.startswith("{"):
                        frame = parse(text)
                        if frame is not None:
                            self._came_up(now)
                            self.on_frame(frame, now)
                    elif text.startswith("<"):
                        d = parse_display(text)
                        if d is None:
                            continue
                        self._came_up(now)
                        if self.mode == "mcr" and self._settled(now) and not self.hold:
                            if self.on_foreign:
                                self._foreign("display frames while MCr was wanted", now)
                            else:
                                await self.request("mcr")
                        if self.on_display:
                            self.on_display(d, now)
                    elif text.startswith("$id:"):
                        log.debug("heartbeat: %s", text)
                    elif text.startswith("XML="):
                        # reply to MC? - the bridge never sends it
                        self._foreign("identify reply (XML=)", now)
                    elif _DUMP_RE.match(text.rstrip()):
                        # setup dump (MCu) - the bridge never asks for it
                        self._foreign("setup dump", now)
                    elif text:
                        log.debug("<< %s", text[:120])
            if now - self._last_rx > WATCHDOG_S:
                raise ConnectionError(f"no data for {WATCHDOG_S:.0f} s")
            if now - last_keepalive >= KEEPALIVE_S:
                writer.write(b"\n")
                await writer.drain()
                last_keepalive = now
            # heartbeat only or silence while MCr is wanted: ask again
            if (self.mode == "mcr" and not self.hold and now - self._last_frame > RESEND_S
                    and now - self._last_mcr > RESEND_S):
                log.info("no frames for %.0f s, re-sending MCr", now - self._last_frame)
                await self.request("mcr")
