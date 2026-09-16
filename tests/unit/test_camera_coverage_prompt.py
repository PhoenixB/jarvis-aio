"""Camera floor-plan coverage → analysis prompt (v7.91.0).

When a camera's floor-plan coverage lists more than the room it's named after
(the Dining Room camera also frames the Living Room), the analysis prompt must
carry every covered area so the model reports on all of them. These test the
pure parsing/hinting that carries that correctness, from the real
floor_plan_cameras shape (dict or JSON string).
"""
import sys
import types

import pytest

# Real shape (trimmed) from a live floor_plan_cameras value.
_FPC = {
    "1f": [
        {"id": "a", "entity": "camera.dining_room", "angle": 45, "fov": 170, "range": 175,
         "coverage": {"covered": ["Dining Room", "Living Room"], "reason": "…", "source": "llm"}},
        {"id": "b", "entity": "camera.kitchen", "angle": 225, "fov": 170, "range": 75,
         "coverage": {"covered": ["Kitchen"], "reason": "…", "source": "llm"}},
        {"id": "c", "entity": "camera.no_coverage_yet"},
    ]
}


@pytest.fixture
def cam(load, monkeypatch):
    # Third-party + heavy siblings camera.py imports at module load but that the
    # pure coverage helpers don't need — stub so the import resolves in isolation.
    monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
    hc = types.ModuleType("homeassistant.components.camera")
    hc.async_get_image = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "homeassistant.components.camera", hc)
    net = types.ModuleType("homeassistant.helpers.network")
    net.get_url = lambda *a, **k: "http://localhost:8123"
    monkeypatch.setitem(sys.modules, "homeassistant.helpers.network", net)
    for sib, fns in {
        "jc.database": {"save_message": lambda *a, **k: None},
        "jc.tts_helper": {"async_announce": lambda *a, **k: None},
        "jc.camera_backends": {"find_backend": lambda *a, **k: None},
    }.items():
        m = types.ModuleType(sib)
        for n, f in fns.items():
            setattr(m, n, f)
        monkeypatch.setitem(sys.modules, sib, m)
    sys.modules.pop("jc.camera", None)
    mod = load("camera")
    yield mod
    sys.modules.pop("jc.camera", None)


def test_parse_coverage_from_dict(cam):
    assert cam._parse_coverage_rooms(_FPC, "camera.dining_room") == ["Dining Room", "Living Room"]
    assert cam._parse_coverage_rooms(_FPC, "camera.kitchen") == ["Kitchen"]


def test_parse_coverage_from_json_string(cam):
    import json
    raw = json.dumps(_FPC)
    assert cam._parse_coverage_rooms(raw, "camera.dining_room") == ["Dining Room", "Living Room"]


def test_parse_coverage_unknown_or_missing(cam):
    assert cam._parse_coverage_rooms(_FPC, "camera.does_not_exist") == []
    assert cam._parse_coverage_rooms(_FPC, "camera.no_coverage_yet") == []
    assert cam._parse_coverage_rooms("", "camera.dining_room") == []
    assert cam._parse_coverage_rooms("not json", "camera.dining_room") == []
    assert cam._parse_coverage_rooms({"1f": "bogus"}, "camera.dining_room") == []


def test_coverage_hint_wording(cam):
    assert cam._coverage_hint([]) == ""
    single = cam._coverage_hint(["Kitchen"])
    assert "Kitchen" in single
    multi = cam._coverage_hint(["Dining Room", "Living Room"])
    assert "Dining Room" in multi and "Living Room" in multi
    assert "EACH" in multi  # instructs the model to report on every covered area
