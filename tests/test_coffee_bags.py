import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from test_mqtt_discovery import _configs, _output  # noqa: E402

from leva2mqtt import telegram, visualizer  # noqa: E402
from leva2mqtt.bridge import Bridge  # noqa: E402
from leva2mqtt.config import load  # noqa: E402
from leva2mqtt.mqtt import NO_BAG, parse_setting  # noqa: E402
from leva2mqtt.outbox import DONE, FAILED, PENDING, Entry, PermanentError  # noqa: E402


class FakeApi:
    """Answers like the Visualizer API (paged lists, bag details)."""

    def __init__(self, roasters, bags, details=None, items=2):
        self.roasters, self.bags, self.details, self.items = roasters, bags, details or {}, items
        self.calls = []

    def __call__(self, method, path, username, password, body=None, **kw):
        self.calls.append((method, path))
        base, _, query = path.partition("?")
        if base.startswith("/coffee_bags/"):
            return self.details[base.rsplit("/", 1)[1]]
        rows = {"/roasters": self.roasters, "/coffee_bags": self.bags}[base]
        page = int(dict(p.split("=") for p in query.split("&"))["page"])
        pages = max(1, -(-len(rows) // self.items))
        chunk = rows[(page - 1) * self.items:page * self.items]
        return {"data": chunk, "paging": {"count": len(rows), "page": page, "pages": pages}}


def test_bag_list_labels_paging_and_archive():
    api = FakeApi(
        roasters=[{"id": "r1", "name": "Tante Ema"}, {"id": "r2", "name": "Kaffeekommune"}],
        bags=[{"id": "b1", "name": "Brasil", "roaster_id": "r1", "archived_at": None},
              {"id": "b2", "name": "Kenia", "roaster_id": "r2", "archived_at": None},
              {"id": "b3", "name": "Old", "roaster_id": "r2", "archived_at": "2026-01-01"},
              {"id": "b4", "name": "Solo", "roaster_id": "gone", "archived_at": None}])
    bags = visualizer.coffee_bags("u", "p", api=api)
    assert bags == [{"id": "b1", "label": "Brasil – Tante Ema"},
                    {"id": "b2", "label": "Kenia – Kaffeekommune"},
                    {"id": "b4", "label": "Solo"}]
    assert ("GET", "/coffee_bags?items=100&page=2") in api.calls


def test_duplicate_labels_get_roast_date_or_id():
    api = FakeApi(
        roasters=[{"id": "r1", "name": "R"}],
        bags=[{"id": "aaaaaa1", "name": "X", "roaster_id": "r1"},
              {"id": "bbbbbb2", "name": "X", "roaster_id": "r1"},
              {"id": "cccccc3", "name": "X", "roaster_id": "r1"}],
        details={"aaaaaa1": {"roast_date": "2026-10-01"}, "bbbbbb2": {"roast_date": "2026-10-01"},
                 "cccccc3": {"roast_date": None}}, items=100)
    labels = [b["label"] for b in visualizer.coffee_bags("u", "p", api=api)]
    assert labels == ["X – R (2026-10-01) #aaaaaa", "X – R (2026-10-01) #bbbbbb",
                      "X – R #cccccc"]
    assert len(set(labels)) == 3


def test_link_rejected_without_coffee_management(monkeypatch):
    monkeypatch.setattr(visualizer, "_api", lambda *a, **k: {"coffee_bag_id": None})
    with pytest.raises(PermanentError):
        visualizer.link_coffee_bag("s1", "b1", "u", "p")
    monkeypatch.setattr(visualizer, "_api", lambda *a, **k: {"coffee_bag_id": "b1"})
    visualizer.link_coffee_bag("s1", "b1", "u", "p")


def test_setting_and_select_entity():
    assert parse_setting("coffee_bag", NO_BAG) == ""
    assert parse_setting("coffee_bag", " Kenia – K ") == "Kenia – K"
    out = _output()
    out.cfg.visualizer.username, out.cfg.visualizer.password = "u", "p"
    out.set_coffee_bags(["Kenia – K"])
    sel = _configs(out)["coffee_bag"]
    assert sel["options"] == [NO_BAG, "Kenia – K"]
    out.published.clear()
    out.set_coffee_bags(None)
    assert out.published == [("homeassistant/select/leva2mqtt_espresso_machine/coffee_bag/config", "")]


def _bridge():
    cfg = load("/nonexistent.yaml", {"MACHINE_HOST": "ito", "VISUALIZER_USERNAME": "u",
                                     "VISUALIZER_PASSWORD": "p", "SHOTS_DIRECTORY": ""})
    return Bridge(cfg)


def test_bridge_selection_follows_bag_list():
    b = _bridge()
    b._on_setting("coffee_bag", "Kenia – K")          # restored before the first load: kept
    assert b.settings["coffee_bag"] == "Kenia – K"
    b.apply_bags([{"id": "b2", "label": "Kenia – K"}])
    b._on_setting("coffee_bag", "Unknown")            # not in the list: ignored
    assert b.settings["coffee_bag"] == "Kenia – K"
    b.apply_bags([{"id": "b1", "label": "Brasil – T"}])   # bag archived: cleared
    assert b.settings["coffee_bag"] == ""


def test_link_waits_for_upload(monkeypatch):
    b = _bridge()
    calls = []
    monkeypatch.setattr(visualizer, "link_coffee_bag", lambda *a: calls.append(a))
    e = Entry("a.json", {"leva2mqtt": {"record": {"coffee_bag_id": "b2"}, "delivery": {
        "visualizer": {"state": PENDING}, "coffee_bag": {"state": PENDING}}}})
    with pytest.raises(RuntimeError):
        asyncio.run(b._send_coffee_bag(e))
    e.delivery["visualizer"] = {"state": FAILED}
    with pytest.raises(PermanentError):
        asyncio.run(b._send_coffee_bag(e))
    e.delivery["visualizer"] = {"state": DONE, "id": "s1", "url": "u"}
    asyncio.run(b._send_coffee_bag(e))
    assert calls == [("s1", "b2", "u", "p")]


def test_telegram_names_the_bag():
    rec = {"start": "2026-10-13T10:00:00+00:00", "coffee_bag": "Kenia – K", "bean": "free text",
           "grinder_setting": "12"}
    text = telegram.format_shot(rec, "Silvia", now=0)
    assert "Kenia – K, grinder 12" in text and "free text" not in text


# --- grinders (from the configuration; Visualizer has no grinder API) ---------

def test_grinders_from_env_and_select():
    cfg = load("/nonexistent.yaml", {"MACHINE_HOST": "ito", "MQTT_HOST": "b",
                                     "SHOTS_GRINDERS": "Niche Zero, DF64 ,"})
    assert cfg.shots.grinders == ["Niche Zero", "DF64"]
    out = _output()
    out.cfg.shots.grinders = cfg.shots.grinders
    out._announce_settings()
    assert _configs(out)["grinder"]["options"] == ["None", "Niche Zero", "DF64"]
    assert parse_setting("grinder", "None") == ""


def test_bridge_grinder_setting_and_shot_json():
    cfg = load("/nonexistent.yaml", {"MACHINE_HOST": "ito", "SHOTS_DIRECTORY": "x",
                                     "SHOTS_GRINDERS": "DF64"})
    b = Bridge(cfg)
    assert b.settings["grinder"] == "DF64"          # single grinder is preselected
    b._on_setting("grinder", "Unknown")
    assert b.settings["grinder"] == "DF64"
    from leva2mqtt.shots import Run
    run = Run.__new__(Run)
    run.samples, run.start, run.profile, run.yield_g = [], 0, None, None
    data = visualizer.to_decent_json(run, grinder_setting="12", grinder_model="DF64")
    assert data["app"]["data"]["settings"]["grinder_model"] == "DF64"
    text = telegram.format_shot({"start": "2026-10-13T10:00:00+00:00", "grinder": "DF64",
                                 "grinder_setting": "12"}, "Silvia", now=0)
    assert "grinder DF64 12" in text
