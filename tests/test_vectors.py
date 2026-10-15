"""Check the bridge's frame parser against the shared vectors in tests/vectors/.

The same JSON file is meant for every client implementation (see docs/client-spec.md),
so a second client (e.g. a mobile app) decodes frames exactly like the bridge.
"""
import json
from pathlib import Path

import pytest

from leva2mqtt.frame import FRAME_LEN, parse

VECTORS = json.loads((Path(__file__).parent / "vectors" / "mcr_frames.json").read_text())["vectors"]


def _state(f):
    if f.standby:
        return "standby"
    if f.eco:
        return "eco"
    return "awake"


@pytest.mark.parametrize("v", VECTORS, ids=[v["name"] for v in VECTORS])
def test_vector(v):
    f = parse(v["line"])
    exp = v["expect"]
    if not exp["valid"]:
        assert f is None
        return
    assert f is not None
    assert len(v["line"]) == FRAME_LEN
    for key, want in exp.items():
        if key == "valid":
            continue
        got = _state(f) if key == "state" else getattr(f, key)
        if isinstance(want, float):
            assert got == pytest.approx(want), key
        else:
            assert got == want, key
