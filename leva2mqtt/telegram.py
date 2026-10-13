"""Shot notification through the Telegram Bot API (works without Home Assistant)."""
from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

from .outbox import PermanentError

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/sendMessage"
LATE_S = 120.0     # a message sent later than this says when the shot was


def format_shot(record: dict, machine: str, url: Optional[str] = None,
                now: Optional[float] = None) -> str:
    """record = shot summary as published on MQTT (see Run.summary + dose/ratio/bean)."""
    lines = [f"☕ {machine}: shot finished"]
    start = datetime.fromisoformat(record["start"])
    now = time.time() if now is None else now
    if now - start.timestamp() > LATE_S:
        lines[0] += f" at {start.astimezone():%H:%M} (sent late)"
    parts = []
    if record.get("duration") is not None:
        parts.append(f"{record['duration']:.0f} s")
    if record.get("yield") is not None:
        out = f"{record['yield']:.1f} g out"
        if record.get("dose"):
            out += f" from {record['dose']:.1f} g (1:{record['yield'] / record['dose']:.1f})"
        parts.append(out)
    elif record.get("volume") is not None:
        parts.append(f"{record['volume']} ml")
    if record.get("peak_pressure") is not None:
        parts.append(f"peak {record['peak_pressure']:.1f} bar")
    if parts:
        lines.append(", ".join(parts))
    if record.get("first_drop") is not None:
        lines.append(f"first drop after {record['first_drop']:.0f} s")
    if record.get("temperature_start") is not None:
        lines.append(f"boiler {record['temperature_start']:.1f} °C at start")
    bean = record.get("coffee_bag") or record.get("bean")
    if bean:
        lines.append(bean + (f", grinder {record['grinder_setting']}"
                             if record.get("grinder_setting") else ""))
    if url:
        lines.append(url)
    return "\n".join(lines)


def send(token: str, chat_id: str, text: str, timeout: float = 15.0) -> None:
    """Blocking – run in a thread. Raises PermanentError when retrying cannot help."""
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text,
                                   "disable_web_page_preview": "true"}).encode()
    req = urllib.request.Request(API.format(token=token), data=body, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            reply = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        if e.code == 400:   # e.g. chat not found, message invalid
            raise PermanentError(f"HTTP 400: {detail}") from None
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None
    if not reply.get("ok"):
        raise RuntimeError(f"Telegram error: {reply.get('description')}")
