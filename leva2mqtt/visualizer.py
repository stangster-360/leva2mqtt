"""Shot export in the JSON format Visualizer.coffee reads as "Decent JSON", and upload.

Visualizer detects the format from the file content; a file that is none of its other
formats is parsed as Decent JSON. Keys used here: timestamp, elapsed, pressure.pressure,
pressure.goal, flow.flow, flow.by_weight, temperature.basket, temperature.goal,
totals.weight, totals.water_dispensed, profile.title, app.data.settings (drink_weight,
bean_weight, bean_brand, grinder_model, grinder_setting, espresso_notes).
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
import uuid
from typing import Optional

from . import __version__
from .outbox import PermanentError
from .shots import Run

log = logging.getLogger(__name__)

API_URL = "https://visualizer.coffee/api"
UPLOAD_URL = API_URL + "/shots/upload"
SHOT_URL = "https://visualizer.coffee/shots/{id}"


def _series(values, scale=1.0, nd=2):
    out, last = [], 0.0
    for v in values:
        if v is not None:
            last = round(v * scale, nd)
        out.append(last)
    return out


def to_decent_json(run: Run, dose: Optional[float] = None, bean: Optional[str] = None,
                   grinder_setting: Optional[str] = None,
                   grinder_model: Optional[str] = None) -> dict:
    # curve ends at the pump stop, so Visualizer's duration equals run.duration;
    # drink_weight still comes from the whole record including the drip (run.yield_g)
    s = run.curve
    data: dict = {
        "version": 2,
        "timestamp": int(run.start),
        "elapsed": [round(x.t, 3) for x in s],
        "pressure": {"pressure": _series(x.pressure for x in s)},
        # flow meter rate is ml/min (seen), Visualizer expects ml/s
        "flow": {"flow": _series((x.flow_rate for x in s), 1 / 60)},
        # the only temperature in the frame is the boiler
        "temperature": {"basket": _series(x.temperature for x in s),
                        "goal": _series(x.setpoint for x in s)},
        "totals": {"water_dispensed": _series((x.volume for x in s), nd=1)},
        "profile": {"title": run.profile or "leva!"},
    }
    if any(x.target_pressure is not None for x in s):
        data["pressure"]["goal"] = _series(x.target_pressure for x in s)
    if any(x.weight is not None for x in s):
        data["totals"]["weight"] = _series((x.weight for x in s), nd=1)
    if any(x.flow_weight is not None for x in s):
        data["flow"]["by_weight"] = _series(x.flow_weight for x in s)
    if any(x.flow_target is not None for x in s):
        # flow tracking target, ml/min -> ml/s; whether Visualizer shows it: unverified
        data["flow"]["goal"] = _series((x.flow_target for x in s), 1 / 60)

    settings: dict = {
        "espresso_notes": f"Recorded by leva2mqtt {__version__}. "
                          "Temperature is the boiler temperature.",
    }
    if run.yield_g is not None:
        settings["drink_weight"] = str(round(run.yield_g, 1))
    if dose:
        settings["bean_weight"] = str(round(dose, 1))
    if bean:
        settings["bean_brand"] = bean
    if grinder_setting:
        settings["grinder_setting"] = grinder_setting
    if grinder_model:
        settings["grinder_model"] = grinder_model
    data["app"] = {"data": {"settings": settings}}
    return data


def extra_series(run: Run) -> Optional[dict]:
    """Series Visualizer has no place for; kept in the shot file only (not uploaded)."""
    s = run.curve
    out = {}
    for key in ("pump_phase", "pressure_min", "pressure_max"):
        if any(getattr(x, key) is not None for x in s):
            out[key] = [getattr(x, key) for x in s]
    return out or None


def _error(e: urllib.error.HTTPError, permanent: tuple[int, ...]) -> Exception:
    detail = e.read().decode(errors="replace")[:200]
    cls = PermanentError if e.code in permanent else RuntimeError
    return cls(f"HTTP {e.code}: {detail}")


def _auth(username: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()


def upload(shot_json: dict, username: str, password: str, timeout: float = 30.0) -> dict:
    """Upload one shot, return {"id": …, "url": …}. Blocking – run in a thread.
    Raises PermanentError when retrying cannot help."""
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="shot.json"\r\n'
        "Content-Type: application/json\r\n\r\n"
    ).encode() + json.dumps(shot_json).encode() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(UPLOAD_URL, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Authorization": _auth(username, password),
        "User-Agent": f"leva2mqtt/{__version__}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            reply = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        # 400/413/415/422: the file itself is rejected, sending it again cannot help.
        # 401/403 (credentials), 429, 5xx: retry later.
        raise _error(e, (400, 413, 415, 422)) from None
    shot_id = reply.get("id")
    if not shot_id:
        raise RuntimeError(f"unexpected reply from Visualizer: {reply}")
    return {"id": shot_id, "url": SHOT_URL.format(id=shot_id)}


# --- coffee bags (needs Premium with coffee management switched on) -----------
#
# API as read from Visualizer's public source (13.10.2026), not yet tried against the
# live service: GET /api/roasters and /api/coffee_bags list {id, name} and
# {id, name, roaster_id, archived_at} with page/items paging (items max. 100);
# GET /api/coffee_bags/<id> has roast_date; PATCH /api/shots/<id> with
# {"shot": {"coffee_bag_id": …}} links a shot, and Visualizer then fills roaster,
# coffee name, roast date and roast level from the bag. Without coffee management the
# key is not permitted and silently dropped.

MAX_PAGES = 10


def _api(method: str, path: str, username: str, password: str, body: Optional[dict] = None,
         timeout: float = 20.0, permanent: tuple[int, ...] = ()) -> dict:
    headers = {"Authorization": _auth(username, password), "Accept": "application/json",
               "User-Agent": f"leva2mqtt/{__version__}"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(API_URL + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise _error(e, permanent) from None


def _all(path: str, username: str, password: str, api=_api) -> list[dict]:
    out: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        sep = "&" if "?" in path else "?"
        reply = api("GET", f"{path}{sep}items=100&page={page}", username, password)
        out.extend(reply.get("data") or [])
        paging = reply.get("paging") or {}
        if not paging.get("pages") or page >= paging["pages"]:
            break
    return out


def coffee_bags(username: str, password: str, api=_api) -> list[dict]:
    """Active (not archived) coffee bags as [{"id", "label"}], in Visualizer's order
    (brewable first, newest roast first). Labels are unique: "<coffee> – <roaster>",
    with the roast date (or a short id) added where two bags would look the same.
    Blocking – run in a thread."""
    roasters = {r.get("id"): r.get("name") or "" for r in _all("/roasters", username,
                                                               password, api)}
    bags = [b for b in _all("/coffee_bags", username, password, api)
            if b.get("id") and not b.get("archived_at")]
    for b in bags:
        roaster = roasters.get(b.get("roaster_id"))
        b["label"] = f"{b.get('name') or '?'} – {roaster}" if roaster else (b.get("name") or "?")
    for _ in range(2):
        counts: dict[str, int] = {}
        for b in bags:
            counts[b["label"]] = counts.get(b["label"], 0) + 1
        dups = [b for b in bags if counts[b["label"]] > 1]
        if not dups:
            break
        for b in dups:
            if "roast_date" not in b:
                b["roast_date"] = api("GET", f"/coffee_bags/{b['id']}", username,
                                      password).get("roast_date")
                if b["roast_date"]:
                    b["label"] += f" ({b['roast_date']})"
                    continue
            b["label"] += f" #{b['id'][:6]}"
    return [{"id": b["id"], "label": b["label"][:255]} for b in bags]


def link_coffee_bag(shot_id: str, bag_id: str, username: str, password: str) -> None:
    """Attach an uploaded shot to a coffee bag. Blocking – run in a thread."""
    # 404: shot or bag gone, 422: bag not the user's – both final
    reply = _api("PATCH", f"/shots/{shot_id}?essentials=1", username, password,
                 {"shot": {"coffee_bag_id": bag_id}}, permanent=(400, 404, 422))
    if "coffee_bag_id" in reply and reply["coffee_bag_id"] != bag_id:
        raise PermanentError("Visualizer did not take the coffee bag – is coffee "
                             "management switched on (Premium)?")
