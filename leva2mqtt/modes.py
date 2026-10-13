"""Who decides the module's report mode: alert polls and other clients.

`MCr` is the data source, but maintenance alerts (`BACKFL`, `DSCALE`, …) only exist in
display frames, and the report mode is global for the module (docs/detection-rules.md):

- **Alert poll** (decided 13.10.): while the machine is idle (awake or eco, no pump
  run, no steam, no hot water) the bridge switches to `MC@` every `interval` seconds,
  reads display frames for `poll_s` seconds and switches back to `MCr`. If the shot
  screen shows up it switches back at once. An alert is cleared only after
  `misses_to_clear` polls without it, because alerts may blink (unverified).
- **Other client** (seen: the Leva Companion app sends `MC?`, `MCu`, `MC@`): the
  bridge stops switching back to `MCr`, so the app keeps its display, and reads what
  the display frames carry. It asks for `MCr` again after `quiet` seconds without
  identify/dump lines and without input events on the display (interval unverified).

The controller is plain logic; the bridge calls it and carries out the returned mode
requests ("mcr" / "display").
"""
from __future__ import annotations

import logging
from typing import Optional

from .display import Display

log = logging.getLogger(__name__)

NORMAL, POLLING, FOREIGN = "normal", "polling", "foreign"


class ModeController:
    def __init__(self, interval: float = 300.0, first_delay: float = 30.0,
                 poll_s: float = 1.0, poll_timeout: float = 3.0, quiet: float = 180.0,
                 misses_to_clear: int = 2, min_frames: int = 3, foreign_window: float = 10.0):
        self.interval, self.first_delay = interval, first_delay
        self.poll_s, self.poll_timeout, self.quiet = poll_s, poll_timeout, quiet
        self.misses_to_clear, self.min_frames = misses_to_clear, min_frames
        self.foreign_window = foreign_window
        self.state = NORMAL
        self.next_poll: Optional[float] = None
        self.changed = False                 # alerts or other_client changed since last read
        self._alerts: Optional[dict[str, int]] = None   # alert -> polls without it
        self._seen: set[str] = set()
        self._frames = 0
        self._start = self._last_activity = self._window_start = 0.0
        self._first: Optional[float] = None

    # --- read side -------------------------------------------------------

    @property
    def alerts(self) -> Optional[tuple[str, ...]]:
        """None until the first evaluation, then the active alerts (may be empty)."""
        return None if self._alerts is None else tuple(sorted(self._alerts))

    @property
    def other_client(self) -> bool:
        return self.state == FOREIGN

    @property
    def polling(self) -> bool:
        return self.state == POLLING

    def take_changed(self) -> bool:
        changed, self.changed = self.changed, False
        return changed

    # --- events ----------------------------------------------------------

    def link_up(self, t: float) -> None:
        self.state = NORMAL
        self.next_poll = t + self.first_delay

    def link_down(self) -> None:
        if self.state == FOREIGN:
            self.changed = True
        self.state = NORMAL
        self.next_poll = None

    def tick(self, t: float, idle: bool) -> Optional[str]:
        if self.state == FOREIGN:
            if t - self._window_start >= self.foreign_window:
                self._evaluate()
                self._window(t)
            if t - self._last_activity >= self.quiet:
                log.info("other client quiet for %.0f s, switching back to MCr", self.quiet)
                self.state = NORMAL
                self.changed = True
                self.next_poll = t + self.interval
                return "mcr"
        elif self.state == POLLING:
            if self._first is not None and t - self._first >= self.poll_s:
                self._evaluate()
                self.state = NORMAL
                return "mcr"
            if t - self._start >= self.poll_timeout:
                log.info("alert poll: no display frames within %.0f s", self.poll_timeout)
                self.state = NORMAL
                return "mcr"
        elif idle and self.next_poll is not None and t >= self.next_poll:
            self.state = POLLING
            self._start, self._first = t, None
            self._window(t)
            self.next_poll = t + self.interval
            return "display"
        return None

    def display(self, d: Display, t: float) -> Optional[str]:
        if self.state == POLLING:
            if d.shot:
                log.info("alert poll: shot screen, back to MCr")
                self.state = NORMAL
                return "mcr"
            self._collect(d)
            if d.main and self._first is None:
                self._first = t
        elif self.state == FOREIGN:
            if d.input_event:
                self._last_activity = t
            self._collect(d)
        if d.ready:
            log.debug("firmware READY shown")
        return None

    def foreign(self, reason: str, t: float) -> bool:
        """Another client is active. Returns True when this starts a foreign phase."""
        self._last_activity = t
        if self.state == FOREIGN:
            return False
        log.warning("another client is using the module (%s); not switching back to "
                    "MCr until it has been quiet for %.0f s", reason, self.quiet)
        self.state = FOREIGN
        self.changed = True
        self._window(t)
        return True

    def cancel_poll(self) -> None:
        """A command of the bridge switches back to MCr anyway (e.g. the wake report)."""
        if self.state == POLLING:
            self.state = NORMAL

    # --- alert evaluation ------------------------------------------------

    def _window(self, t: float) -> None:
        self._window_start = t
        self._seen = set()
        self._frames = 0

    def _collect(self, d: Display) -> None:
        if d.main and not d.standby and not d.shot:
            self._frames += 1
            self._seen.update(d.alerts)

    def _evaluate(self) -> None:
        if self._frames < self.min_frames:
            return
        old = self.alerts
        alerts = dict(self._alerts or {})
        for a in self._seen:
            alerts[a] = 0
        for a in list(alerts):
            if a not in self._seen:
                alerts[a] += 1
                if alerts[a] >= self.misses_to_clear:
                    del alerts[a]
        self._alerts = alerts
        if self.alerts != old:
            log.info("alerts: %s", ", ".join(self.alerts) or "none")
            self.changed = True
        self._window(self._window_start)
