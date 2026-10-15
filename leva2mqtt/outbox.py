"""Persistent queue for finished shots.

Every shot is written to `<directory>/pending/` before anything is sent, so a crash, a
restart or a network outage cannot lose it. Each configured target (Visualizer upload,
coffee bag link, Telegram message) is then delivered; failed deliveries are retried with a growing
delay, also after a restart of the bridge.

When nothing is pending any more:
- deleted, if the Visualizer upload succeeded and `keep` is off (default)
- moved to `<directory>/` (archive) otherwise: `keep` is on, there is no Visualizer
  target (the file is the only copy of the curves), or the upload failed for good

A shot file is a Decent JSON shot that Visualizer accepts as is (it can be uploaded by
hand), plus a `leva2mqtt` key with the summary record and the delivery state.

Without a directory the queue is kept in memory only and is lost on restart.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

log = logging.getLogger(__name__)

PENDING, DONE, FAILED, EXPIRED, SKIPPED = "pending", "done", "failed", "expired", "skipped"
# processing order: the bag link needs the uploaded shot, the message gets the link
TARGETS = ("visualizer", "coffee_bag", "telegram")
BACKOFF = (60, 300, 900, 1800, 3600)          # s after the 1st, 2nd, … failed pass
TELEGRAM_MAX_AGE = 3600.0                     # an older shot message is dropped, not sent
POLL_S = 15.0


class PermanentError(Exception):
    """A delivery that will never succeed (e.g. the server rejects the file)."""


@dataclass
class Entry:
    name: str           # file name, e.g. 2026-10-12_142840_shot.json
    data: dict          # Decent JSON + "leva2mqtt": {"record", "delivery", "created", "failures"}
    next_try: float = 0.0

    @property
    def meta(self) -> dict:
        return self.data["leva2mqtt"]

    @property
    def record(self) -> dict:
        return self.meta["record"]

    @property
    def delivery(self) -> dict:
        return self.meta["delivery"]

    @property
    def shot(self) -> dict:
        """The Decent JSON shot without the bridge's own key."""
        return {k: v for k, v in self.data.items() if k != "leva2mqtt"}

    def pending(self) -> list[str]:
        return [t for t in TARGETS if self.delivery.get(t, {}).get("state") == PENDING]


Sender = Callable[[Entry], Awaitable[Optional[dict]]]


class Outbox:
    """senders: target name -> async function(entry) returning extra fields to store
    (e.g. {"url": …}) or raising PermanentError / any other exception (= retry).
    Only configured targets have a sender."""

    def __init__(self, directory: Optional[str], keep: bool, senders: dict[str, Sender],
                 on_delivered: Optional[Callable[[Entry, str, dict], None]] = None,
                 on_change: Optional[Callable[[int], None]] = None,
                 clock: Callable[[], float] = time.time):
        self.dir = directory or None
        self.pending_dir = os.path.join(self.dir, "pending") if self.dir else None
        self.keep = keep
        self.senders = senders
        self.on_delivered = on_delivered
        self.on_change = on_change
        self.clock = clock
        self.entries: dict[str, Entry] = {}
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()

    # --- public ----------------------------------------------------------

    def load(self) -> None:
        """Pick up shots left in pending/ by a previous run."""
        if not self.pending_dir:
            return
        os.makedirs(self.pending_dir, exist_ok=True)
        for name in sorted(os.listdir(self.pending_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.pending_dir, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    data = json.load(fh)
                data["leva2mqtt"]["delivery"]
            except (OSError, ValueError, KeyError, TypeError) as e:
                log.error("cannot read %s (%s), renamed to .bad", path, e)
                os.replace(path, path + ".bad")
                continue
            self.entries[name] = Entry(name, data)
        if self.entries:
            log.info("%d shot(s) pending from a previous run", len(self.entries))
        self._changed()

    def add(self, name: str, shot: dict, record: dict, targets: list[str],
            extra: Optional[dict] = None) -> Entry:
        delivery = {t: {"state": PENDING if t in targets else SKIPPED, "attempts": 0}
                    for t in TARGETS}
        meta = {"record": record, "delivery": delivery, "created": self.clock(), "failures": 0}
        if extra:
            meta["series"] = extra     # stays in the file, Entry.shot leaves it out
        data = {**shot, "leva2mqtt": meta}
        e = Entry(name, data)
        self.entries[name] = e
        self._save(e)
        self._changed()
        self._wake.set()
        return e

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.process_due()
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), POLL_S)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    async def process_due(self) -> None:
        now = self.clock()
        for e in [e for e in self.entries.values() if e.next_try <= now]:
            await self._process(e)

    # --- internals -------------------------------------------------------

    async def _process(self, e: Entry) -> None:
        failed = False
        for target in TARGETS:
            d = e.delivery.setdefault(target, {"state": SKIPPED, "attempts": 0})
            if d["state"] != PENDING:
                continue
            sender = self.senders.get(target)
            if sender is None:
                log.warning("%s: %s is not configured any more, skipped", e.name, target)
                d["state"] = SKIPPED
                continue
            if target == "telegram" and self.clock() - e.meta["created"] > TELEGRAM_MAX_AGE:
                log.warning("%s: Telegram message not sent within %.0f min, dropped",
                            e.name, TELEGRAM_MAX_AGE / 60)
                d["state"] = EXPIRED
                continue
            d["attempts"] += 1
            try:
                info = await sender(e) or {}
            except PermanentError as err:
                log.error("%s: %s failed for good: %s", e.name, target, err)
                d.update(state=FAILED, error=str(err))
                continue
            except Exception as err:   # network, server, auth – try again later
                log.warning("%s: %s failed (attempt %d): %s", e.name, target, d["attempts"], err)
                d["error"] = str(err)
                failed = True
                continue
            d.pop("error", None)
            d.update(state=DONE, **info)
            log.info("%s: %s done", e.name, target)
            if self.on_delivered:
                try:
                    self.on_delivered(e, target, info)
                except Exception:
                    log.exception("on_delivered failed")
        if e.pending():
            if failed:
                e.meta["failures"] += 1
            delay = BACKOFF[min(e.meta["failures"], len(BACKOFF)) - 1] if e.meta["failures"] else 0
            e.next_try = self.clock() + delay
            log.info("%s: retry in %d s", e.name, delay)
            self._save(e)
        else:
            self._finish(e)
        self._changed()

    def _finish(self, e: Entry) -> None:
        del self.entries[e.name]
        if not self.dir:
            return
        pending_path = os.path.join(self.pending_dir, e.name)
        uploaded = e.delivery.get("visualizer", {}).get("state") == DONE
        try:
            if uploaded and not self.keep:
                os.remove(pending_path)
                log.info("%s: delivered, file deleted", e.name)
            else:
                path = os.path.join(self.dir, e.name)
                self._write(path, e.data)
                os.remove(pending_path)
                log.info("%s: archived to %s", e.name, path)
        except FileNotFoundError:
            pass
        except OSError as err:
            log.error("%s: cannot finish file: %s", e.name, err)

    def _save(self, e: Entry) -> None:
        if not self.pending_dir:
            return
        try:
            os.makedirs(self.pending_dir, exist_ok=True)
            self._write(os.path.join(self.pending_dir, e.name), e.data)
        except OSError as err:
            log.error("%s: cannot write shot file: %s", e.name, err)

    @staticmethod
    def _write(path: str, data: dict) -> None:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def _changed(self) -> None:
        if self.on_change:
            self.on_change(len(self.entries))
