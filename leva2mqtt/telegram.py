"""Shot notification through the Telegram Bot API (works without Home Assistant)."""
from __future__ import annotations

import html
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


TEXTS = {
    "en": {"title": "Shot finished", "late": "Shot from {time} <i>(sent late)</i>",
           "from": "from", "peak": "peak", "first_drop": "first drop after {s} s",
           "boiler": "Boiler {t} °C at start", "link": "View on Visualizer"},
    "de": {"title": "Shot fertig", "late": "Shot von {time} <i>(verspätet gesendet)</i>",
           "from": "aus", "peak": "Spitze", "first_drop": "erster Tropfen nach {s} s",
           "boiler": "Kessel {t} °C beim Start", "link": "Auf Visualizer ansehen"},
}
LANGUAGES = tuple(TEXTS)


def _num(value: float, digits: int, lang: str) -> str:
    text = f"{value:.{digits}f}"
    return text.replace(".", ",") if lang == "de" else text


def format_shot(record: dict, machine: str, url: Optional[str] = None,
                now: Optional[float] = None, language: str = "en") -> str:
    """Telegram message (parse_mode HTML) for one shot.

    record = shot summary as published on MQTT (Run.summary + dose, ratio, bean, …).
    Lines whose values are missing are left out. Names from Visualizer or the
    configuration are escaped, otherwise Telegram rejects the message."""
    lang = language if language in TEXTS else "en"
    t = TEXTS[lang]
    esc = html.escape

    def n(v, d=1):
        return _num(v, d, lang)

    start = datetime.fromisoformat(record["start"])
    now = time.time() if now is None else now
    # headline: the coffee; without one, machine name and "Shot finished"
    coffee = record.get("coffee_bag") or record.get("bean")
    lines = [f"🫘 <b>{esc(coffee)}</b>" if coffee
             else f"☕ <b>{esc(machine)}</b> · {t['title']}"]
    if now - start.timestamp() > LATE_S:
        lines.append("🕓 " + t["late"].format(time=f"{start.astimezone():%H:%M}"))

    parts = []
    if record.get("duration") is not None:
        parts.append(f"<b>{n(record['duration'])} s</b>")
    if record.get("yield") is not None:
        out = f"<b>{n(record['yield'])} g</b>"
        if record.get("dose"):
            ratio = n(record["yield"] / record["dose"])
            out += f" {t['from']} {n(record['dose'])} g (1:{ratio})"
        parts.append(out)
    elif record.get("volume") is not None:
        parts.append(f"<b>{record['volume']} ml</b>")
    if parts:
        lines.append("⏱ " + " · ".join(parts))
    lines.append("")

    parts = []
    if record.get("peak_pressure") is not None:
        parts.append(f"📈 {t['peak']} <b>{n(record['peak_pressure'])} bar</b>")
    if record.get("first_drop") is not None:
        parts.append("💧 " + t["first_drop"].format(s=f"{record['first_drop']:.0f}"))
    if parts:
        lines.append("   ".join(parts))
    if record.get("temperature_start") is not None:
        lines.append("🌡 " + t["boiler"].format(t=n(record["temperature_start"])))

    grind = " · ".join(x for x in (record.get("grinder"), record.get("grinder_setting")) if x)
    if grind:
        lines.append(f"⚙️ {esc(grind)}")
    if lines[-1] == "":
        lines.pop()
    if url:
        lines += ["", f'📊 <a href="{esc(url, quote=True)}">{t["link"]}</a>']
    while lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def send(token: str, chat_id: str, text: str, timeout: float = 15.0) -> None:
    """Blocking – run in a thread. Raises PermanentError when retrying cannot help."""
    body = urllib.parse.urlencode({"chat_id": chat_id, "text": text, "parse_mode": "HTML",
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
