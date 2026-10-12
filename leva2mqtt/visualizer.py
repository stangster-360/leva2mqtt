"""Shot export in the JSON format Visualizer.coffee reads as "Decent JSON", and upload.

Visualizer detects the format from the file content; a file that is none of its other
formats is parsed as Decent JSON. Keys used here: timestamp, elapsed, pressure.pressure,
pressure.goal, flow.flow, flow.by_weight, temperature.basket, temperature.goal,
totals.weight, totals.water_dispensed, profile.title, app.data.settings.
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

UPLOAD_URL = "https://visualizer.coffee/api/shots/upload"
SHOT_URL = "https://visualizer.coffee/shots/{id}"


def _series(values, scale=1.0, nd=2):
    out, last = [], 0.0
    for v in values:
        if v is not None:
            last = round(v * scale, nd)
        out.append(last)
    return out


def to_decent_json(run: Run, dose: Optional[float] = None, bean: Optional[str] = None,
                   grinder_setting: Optional[str] = None) -> dict:
    s = run.samples
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
    data["app"] = {"data": {"settings": settings}}
    return data


def upload(shot_json: dict, username: str, password: str, timeout: float = 30.0) -> str:
    """Upload one shot, return its Visualizer URL. Blocking – run in a thread.
    Raises PermanentError when retrying cannot help."""
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="shot.json"\r\n'
        "Content-Type: application/json\r\n\r\n"
    ).encode() + json.dumps(shot_json).encode() + f"\r\n--{boundary}--\r\n".encode()
    auth = base64.b64encode(f"{username}:{password}".encode()).decode()
    req = urllib.request.Request(UPLOAD_URL, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Authorization": f"Basic {auth}",
        "User-Agent": f"leva2mqtt/{__version__}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            reply = json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:200]
        # 400/413/415/422: the file itself is rejected, sending it again cannot help.
        # 401/403 (credentials), 429, 5xx: retry later.
        if e.code in (400, 413, 415, 422):
            raise PermanentError(f"HTTP {e.code}: {detail}") from None
        raise RuntimeError(f"HTTP {e.code}: {detail}") from None
    shot_id = reply.get("id")
    if not shot_id:
        raise RuntimeError(f"unexpected reply from Visualizer: {reply}")
    return SHOT_URL.format(id=shot_id)
