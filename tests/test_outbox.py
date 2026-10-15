import asyncio
import json
import os

from leva2mqtt.outbox import (BACKOFF, DONE, EXPIRED, FAILED, PENDING, TELEGRAM_MAX_AGE,
                              Outbox, PermanentError)

SHOT = {"version": 2, "timestamp": 1, "elapsed": [0, 0.25]}
REC = {"kind": "shot", "start": "2026-10-12T10:31:11+00:00", "yield": 42.3}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Sender:
    """Fails `fail` times with `exc`, then succeeds."""

    def __init__(self, fail=0, exc=RuntimeError("offline"), info=None):
        self.fail, self.exc, self.info, self.calls = fail, exc, info, []

    async def __call__(self, entry):
        self.calls.append(entry.name)
        if self.fail:
            self.fail -= 1
            raise self.exc
        return self.info


def make(tmp_path, senders, keep=False, clock=None):
    return Outbox(str(tmp_path), keep, senders, clock=clock or Clock())


def files(tmp_path):
    out = set()
    for root, _, names in os.walk(tmp_path):
        for n in names:
            out.add(os.path.relpath(os.path.join(root, n), tmp_path))
    return out


def run(coro):
    return asyncio.run(coro)


def test_file_written_before_delivery(tmp_path):
    ob = make(tmp_path, {"visualizer": Sender()})
    ob.add("a.json", SHOT, REC, ["visualizer"])
    assert files(tmp_path) == {"pending/a.json"}
    data = json.load(open(tmp_path / "pending/a.json"))
    assert data["version"] == 2 and data["leva2mqtt"]["delivery"]["visualizer"]["state"] == PENDING


def test_deleted_after_upload_and_message(tmp_path):
    vis, tg = Sender(info={"url": "https://v/1"}), Sender()
    seen = {}
    ob = make(tmp_path, {"visualizer": vis, "telegram": tg})
    ob.on_delivered = lambda e, t, info: seen.setdefault(t, info)
    ob.add("a.json", SHOT, REC, ["visualizer", "telegram"])
    run(ob.process_due())
    assert vis.calls == ["a.json"] and tg.calls == ["a.json"]
    assert seen["visualizer"] == {"url": "https://v/1"}
    assert files(tmp_path) == set() and not ob.entries


def test_keep_archives(tmp_path):
    ob = make(tmp_path, {"visualizer": Sender(info={"url": "u"})}, keep=True)
    ob.add("a.json", SHOT, REC, ["visualizer"])
    run(ob.process_due())
    assert files(tmp_path) == {"a.json"}
    data = json.load(open(tmp_path / "a.json"))
    assert data["leva2mqtt"]["delivery"]["visualizer"] == {"state": DONE, "attempts": 1, "url": "u"}


def test_no_upload_target_archives(tmp_path):
    ob = make(tmp_path, {"telegram": Sender()})
    ob.add("a.json", SHOT, REC, ["telegram"])
    run(ob.process_due())
    assert files(tmp_path) == {"a.json"}       # only copy of the curves
    ob.add("b.json", SHOT, REC, [])
    run(ob.process_due())
    assert files(tmp_path) == {"a.json", "b.json"}


def test_retry_with_backoff_and_message_not_repeated(tmp_path):
    clock = Clock()
    vis, tg = Sender(fail=2), Sender()
    ob = make(tmp_path, {"visualizer": vis, "telegram": tg}, clock=clock)
    ob.add("a.json", SHOT, REC, ["visualizer", "telegram"])
    run(ob.process_due())
    assert len(tg.calls) == 1                  # message goes out without waiting for the upload
    assert files(tmp_path) == {"pending/a.json"}
    clock.t += BACKOFF[0] - 1
    run(ob.process_due())
    assert len(vis.calls) == 1                 # not due yet
    clock.t += 1
    run(ob.process_due())
    assert len(vis.calls) == 2
    clock.t += BACKOFF[1]
    run(ob.process_due())
    assert len(vis.calls) == 3 and len(tg.calls) == 1
    assert files(tmp_path) == set()


def test_pending_survives_restart(tmp_path):
    ob = make(tmp_path, {"visualizer": Sender(fail=1)})
    ob.add("a.json", SHOT, REC, ["visualizer"])
    run(ob.process_due())
    assert files(tmp_path) == {"pending/a.json"}
    vis = Sender(info={"url": "u"})
    ob2 = make(tmp_path, {"visualizer": vis})
    ob2.load()
    assert list(ob2.entries) == ["a.json"]
    run(ob2.process_due())                     # retried at once after a restart
    assert vis.calls == ["a.json"] and files(tmp_path) == set()


def test_permanent_error_archives(tmp_path):
    ob = make(tmp_path, {"visualizer": Sender(fail=1, exc=PermanentError("HTTP 422"))})
    ob.add("a.json", SHOT, REC, ["visualizer"])
    run(ob.process_due())
    data = json.load(open(tmp_path / "a.json"))
    assert data["leva2mqtt"]["delivery"]["visualizer"]["state"] == FAILED


def test_old_message_is_dropped(tmp_path):
    clock = Clock()
    vis, tg = Sender(fail=1), Sender(fail=1)
    ob = make(tmp_path, {"visualizer": vis, "telegram": tg}, clock=clock)
    ob.add("a.json", SHOT, REC, ["visualizer", "telegram"])
    run(ob.process_due())
    clock.t += TELEGRAM_MAX_AGE + 1
    run(ob.process_due())
    assert len(tg.calls) == 1 and len(vis.calls) == 2
    assert files(tmp_path) == set()            # upload done -> deleted, message expired


def test_target_no_longer_configured(tmp_path):
    ob = make(tmp_path, {"visualizer": Sender(fail=1)})
    ob.add("a.json", SHOT, REC, ["visualizer"])
    run(ob.process_due())
    ob2 = make(tmp_path, {})
    ob2.load()
    run(ob2.process_due())
    assert files(tmp_path) == {"a.json"}


def test_broken_file_is_set_aside(tmp_path):
    os.makedirs(tmp_path / "pending")
    (tmp_path / "pending/x.json").write_text("{not json")
    ob = make(tmp_path, {})
    ob.load()
    assert not ob.entries and files(tmp_path) == {"pending/x.json.bad"}


def test_memory_only(tmp_path):
    vis = Sender(fail=1)
    ob = Outbox(None, False, {"visualizer": vis}, clock=Clock())
    ob.add("a.json", SHOT, REC, ["visualizer"])
    run(ob.process_due())
    assert "a.json" in ob.entries and files(tmp_path) == set()


def test_expired_state_recorded(tmp_path):
    clock = Clock()
    ob = make(tmp_path, {"telegram": Sender(fail=5)}, clock=clock)
    ob.add("a.json", SHOT, REC, ["telegram"])
    run(ob.process_due())
    clock.t += TELEGRAM_MAX_AGE + 1
    run(ob.process_due())
    data = json.load(open(tmp_path / "a.json"))
    assert data["leva2mqtt"]["delivery"]["telegram"]["state"] == EXPIRED


def test_telegram_text():
    from leva2mqtt.telegram import format_shot
    rec = {**REC, "duration": 21.4, "dose": 18.0, "peak_pressure": 7.7, "first_drop": 9.3,
           "temperature_start": 90.8, "coffee_bag": "CUERPO – Südhang", "grinder": "DF64",
           "grinder_setting": "2,6"}
    start = 1791801071.0   # 10:31:11 UTC
    de = format_shot(rec, "Silvia", "https://v/1?a=1&b=2", now=start + 30, language="de")
    assert de.splitlines() == [
        "☕ <b>Silvia</b> · Shot fertig",
        "",
        "⏱ <b>21 s</b> · <b>42,3 g</b> aus 18,0 g (1:2,3)",
        "📈 Spitze <b>7,7 bar</b>   💧 erster Tropfen nach 9 s",
        "🌡 Kessel 90,8 °C beim Start",
        "",
        "🫘 CUERPO – Südhang",
        "⚙️ DF64 · 2,6",
        "",
        '📊 <a href="https://v/1?a=1&amp;b=2">Auf Visualizer ansehen</a>',
    ]
    en = format_shot(rec, "Silvia", now=start + 600)
    assert "Shot from" in en and "(sent late)" in en and "42.3 g</b> from 18.0 g (1:2.3)" in en
    assert not en.endswith("\n") and "📊" not in en


def test_telegram_escapes_names_and_skips_empty_lines():
    from leva2mqtt.telegram import format_shot
    text = format_shot({"start": REC["start"], "bean": "<Blend> & Co"}, "A<B", now=0)
    assert "&lt;Blend&gt; &amp; Co" in text and "A&lt;B" in text
    assert "⏱" not in text and "🌡" not in text and "⚙️" not in text


def test_telegram_language_config():
    import pytest
    from leva2mqtt.config import ConfigError, load
    assert load("/x", {"MACHINE_HOST": "h", "TELEGRAM_LANGUAGE": "DE"}).telegram.language == "de"
    with pytest.raises(ConfigError):
        load("/x", {"MACHINE_HOST": "h", "TELEGRAM_LANGUAGE": "fr"})
