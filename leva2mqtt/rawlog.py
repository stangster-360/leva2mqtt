"""Raw recording of the telnet stream while the bridge runs (`capture.enabled`).

Writes the same format as tools/capture.py, so replay.py and fake_ito.py read it as is:

    <unix time> <line>      every line received from the module
    <unix time> # <text>    events: connect / lost, commands sent (`>> MCr`), bridge
                            decisions (state, power mode, finished runs), notes from HA

One file per session: it starts when the connection to the module comes up and ends
when the connection is lost – with a machine switched by a smart plug, one file per
switch-on. A reconnect within REJOIN_S (WLAN hiccup, watchdog) continues the same file.
A session longer than MAX_FILE_S (module powered all the time) starts a new file.
Names: `<YYYY-MM-DD>_<HHMMSS>_bridge.txt` (start, local time). Files older than
`keep_days` are deleted when a new file starts. Nothing is written outside a session,
so a file's first line is close to its start (replay tools time from the first line).

Recording is passive: it never sends anything. A write error disables it (logged once)
instead of stopping the bridge.
"""
from __future__ import annotations

import logging
import os
import re
import time
from datetime import date, datetime, timedelta
from typing import Callable, Optional, TextIO

log = logging.getLogger(__name__)

SUFFIX = "_bridge.txt"
REJOIN_S = 60.0           # reconnect within this continues the file
MAX_FILE_S = 24 * 3600.0  # start a new file after this
_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_\d{6}_bridge\.txt$")


class RawLog:
    def __init__(self, directory: str, keep_days: int = 7,
                 clock: Callable[[], float] = time.time):
        self.directory = directory
        self.keep_days = max(1, int(keep_days))
        self.clock = clock
        self._fh: Optional[TextIO] = None
        self._path: Optional[str] = None
        self._opened = 0.0          # start of the current file
        self._ended: Optional[float] = None   # end of the last session
        self._broken = False
        self._announced = False

    @property
    def path(self) -> Optional[str]:
        return self._path

    # --- sessions --------------------------------------------------------

    def start(self, t: float) -> None:
        """The connection to the module is up."""
        if self._broken or self._fh:
            return
        rejoin = (self._path and self._ended is not None and t - self._ended < REJOIN_S
                  and t - self._opened < MAX_FILE_S)
        try:
            if rejoin:
                self._fh = open(self._path, "a", encoding="utf-8", buffering=1)
            else:
                self._new_file(t)
        except OSError as e:
            self._fail(e)

    def end(self, t: float) -> None:
        """The connection is gone (or the bridge stops)."""
        if self._fh:
            self._close_file()
            self._ended = t

    def close(self) -> None:
        self.end(self.clock())

    def _new_file(self, t: float) -> None:
        os.makedirs(self.directory, exist_ok=True)
        start = datetime.fromtimestamp(t)
        self._path = os.path.join(self.directory, f"{start:%Y-%m-%d_%H%M%S}{SUFFIX}")
        self._fh = open(self._path, "a", encoding="utf-8", buffering=1)
        self._opened = t
        if not self._announced:
            self._announced = True
            log.info("raw capture to %s (kept %d days)", self.directory, self.keep_days)
        log.info("raw capture: %s", os.path.basename(self._path))
        self.prune(start.date())

    # --- writing ---------------------------------------------------------

    def line(self, t: float, text: str) -> None:
        """A line received from the module."""
        if text:
            self._write(t, text)

    def note(self, text: str, t: Optional[float] = None) -> None:
        """An event line (`# text`). Dropped outside a session."""
        self._write(self.clock() if t is None else t, "# " + text.replace("\n", " "))

    def _write(self, t: float, text: str) -> None:
        if self._fh is None:
            return
        try:
            if t - self._opened >= MAX_FILE_S:
                self._close_file()
                self._new_file(t)
            self._fh.write(f"{t:.3f} {text}\n")
        except OSError as e:
            self._fail(e)

    def _fail(self, e: OSError) -> None:
        self._broken = True
        log.warning("raw capture disabled, cannot write to %s: %s", self.directory, e)
        self._close_file()

    def _close_file(self) -> None:
        if self._fh:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    # --- retention -------------------------------------------------------

    def prune(self, today: date) -> None:
        """Delete files that started more than keep_days ago (today counts as day 1)."""
        oldest = today - timedelta(days=self.keep_days - 1)
        try:
            names = os.listdir(self.directory)
        except OSError:
            return
        for name in names:
            m = _NAME_RE.match(name)
            if not m:
                continue
            try:
                if date.fromisoformat(m.group(1)) < oldest:
                    os.remove(os.path.join(self.directory, name))
                    log.info("raw capture: deleted %s", name)
            except (OSError, ValueError) as e:
                log.warning("raw capture: cannot delete %s: %s", name, e)
