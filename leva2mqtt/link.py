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
- display frames (`<…>`) instead of `MCr` frames: someone switched the module to
  `MC@`; re-send `MCr` as well
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Callable, Optional

from .frame import Frame, parse

log = logging.getLogger(__name__)

PORT = 23
CONNECT_TIMEOUT_S = 3.0
RETRY_S = 2.0
KEEPALIVE_S = 5.0
WATCHDOG_S = 3.0
RESEND_S = 5.0

# Only these commands are ever sent. Others change setpoints or PID values
# (MC+, MC-, MCp, MCi, MCd, MCw, …) or toggle reporting (MC<space>).
ALLOWED_COMMANDS = {
    "MCr",            # report sensor data continuously
    "MCa",            # wake from eco / standby
    "MCe",            # enter eco
    "MCE",            # enter standby
    "MCcREPORT SP1",  # beep setpoint 1 (also wakes the machine)
}

FrameHandler = Callable[[Frame, float], None]
LinkHandler = Callable[[bool, float, Optional[float]], None]


class Link:
    """Keeps one connection to the module and delivers decoded `MCr` frames.

    on_frame(frame, t)                  for every `MCr` frame (t = arrival, unix time)
    on_link(up, t, offline_since)       up=True on the first frame of a connection;
                                        offline_since = when frames were last seen
                                        (or the first failed connect), None if the
                                        bridge has never been offline before
                                        up=False when the connection is lost
    """

    def __init__(self, host: str, port: int, on_frame: FrameHandler, on_link: LinkHandler,
                 clock: Callable[[], float] = time.time):
        self.host, self.port = host, port
        self.on_frame, self.on_link = on_frame, on_link
        self.clock = clock
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

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        now = self.clock()
        self._last_rx = now
        self._last_frame = now
        last_keepalive = now
        last_mcr = now
        await self.send("MCr")
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
                        if frame is None:
                            continue
                        self._last_frame = now
                        if not self._up:
                            self._up = True
                            offline = self._offline_since
                            self._offline_since = None
                            self.on_link(True, now, offline)
                        self.on_frame(frame, now)
                    elif text.startswith("$id:"):
                        log.debug("heartbeat: %s", text)
                    elif text:
                        log.debug("<< %s", text[:120])
            if now - self._last_rx > WATCHDOG_S:
                raise ConnectionError(f"no data for {WATCHDOG_S:.0f} s")
            if now - last_keepalive >= KEEPALIVE_S:
                writer.write(b"\n")
                await writer.drain()
                last_keepalive = now
            # heartbeat-only, display frames or nothing: ask for MCr again
            if now - self._last_frame > RESEND_S and now - last_mcr > RESEND_S:
                log.info("no MCr frames for %.0f s, re-sending MCr", now - self._last_frame)
                await self.send("MCr")
                last_mcr = now
