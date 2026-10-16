"""Raw recording of the telnet stream while the bridge runs (`capture.enabled`).

Writes the same format as tools/capture.py, so replay.py and fake_ito.py read it as is:

    <unix time> <line>      every line received from the module
    <unix time> # <text>    events: connect / lost, commands sent (`>> MCr`), bridge
                            decisions (state, power mode, finished runs), notes from HA

One file per local day, `<YYYY-MM-DD>_bridge.txt`, appended to after a restart. Files of
earlier days are gzip-compressed; files older than `keep_days` are deleted. Recording is
passive: it never sends anything. A write error disables it (logged once) instead of
stopping the bridge.
"""
from __future__ import annotations

import gzip
import logging
import os
import re
import shutil
import threading
import time
from datetime import date, datetime, timedelta
from typing import Callable, Optional, TextIO

log = logging.getLogger(__name__)

SUFFIX = "_bridge.txt"
_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_bridge\.txt(\.gz)?$")


class RawLog:
    def __init__(self, directory: str, keep_days: int = 7,
                 clock: Callable[[], float] = time.time, background: bool = True):
        self.directory = directory
        self.keep_days = max(1, int(keep_days))
        self.clock = clock
        self.background = background
        self._day: Optional[date] = None
        self._fh: Optional[TextIO] = None
        self._broken = False
        self._lock = threading.Lock()

    # --- writing ---------------------------------------------------------

    def line(self, t: float, text: str) -> None:
        """A line received from the module."""
        if text:
            self._write(t, text)

    def note(self, text: str, t: Optional[float] = None) -> None:
        """An event line (`# text`)."""
        self._write(self.clock() if t is None else t, "# " + text.replace("\n", " "))

    def _write(self, t: float, text: str) -> None:
        if self._broken:
            return
        try:
            day = datetime.fromtimestamp(t).date()
            if day != self._day:
                self._open(day)
            self._fh.write(f"{t:.3f} {text}\n")
        except OSError as e:
            self._broken = True
            log.warning("raw capture disabled, cannot write to %s: %s", self.directory, e)
            self._close_file()

    def _open(self, day: date) -> None:
        self._close_file()
        os.makedirs(self.directory, exist_ok=True)
        path = os.path.join(self.directory, f"{day.isoformat()}{SUFFIX}")
        self._fh = open(path, "a", encoding="utf-8", buffering=1)
        first = self._day is None
        self._day = day
        if first:
            log.info("raw capture to %s (kept %d days)", self.directory, self.keep_days)
        self._housekeeping()

    def _close_file(self) -> None:
        if self._fh:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def close(self) -> None:
        self._close_file()

    # --- compress and prune ----------------------------------------------

    def _housekeeping(self) -> None:
        today = self._day
        if self.background:
            threading.Thread(target=self.tidy, args=(today,), daemon=True,
                             name="rawlog-tidy").start()
        else:
            self.tidy(today)

    def tidy(self, today: date) -> None:
        """Compress files of earlier days, delete files older than keep_days."""
        with self._lock:
            oldest = today - timedelta(days=self.keep_days - 1)
            try:
                names = os.listdir(self.directory)
            except OSError:
                return
            for name in sorted(names):
                m = _NAME_RE.match(name)
                if not m:
                    continue
                path = os.path.join(self.directory, name)
                try:
                    day = date.fromisoformat(m.group(1))
                    if day < oldest:
                        os.remove(path)
                        log.info("raw capture: deleted %s", name)
                    elif day < today and not m.group(2):
                        _compress(path)
                except (OSError, ValueError) as e:
                    log.warning("raw capture: cannot tidy %s: %s", name, e)


def _compress(path: str) -> None:
    tmp = path + ".gz.part"
    with open(path, "rb") as src, gzip.open(tmp, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, 1 << 20)
    os.replace(tmp, path + ".gz")
    os.remove(path)
