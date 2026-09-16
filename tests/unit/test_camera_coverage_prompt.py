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


# ── Issue A: Frigate grounding + low-light hardening (v7.92.0) ────────────────
def test_frigate_detections_reads_occupancy(cam, fake_hass):
    fake_hass.states.set("binary_sensor.front_yard_person", "on")
    fake_hass.states.set("binary_sensor.front_yard_car", "off")
    dets = cam._frigate_detections(fake_hass, "camera.front_yard")
    assert dets == {"person": True, "car": False}


def test_frigate_detections_count_sensor(cam, fake_hass):
    fake_hass.states.set("sensor.driveway_car", "2")
    fake_hass.states.set("binary_sensor.driveway_person", "off")
    dets = cam._frigate_detections(fake_hass, "camera.driveway")
    assert dets["car"] is True and dets["person"] is False


def test_frigate_detections_none_when_absent(cam, fake_hass):
    # No Frigate object entities for this camera → None (grounding skipped, never
    # a false "empty scene").
    assert cam._frigate_detections(fake_hass, "camera.nonexistent") is None


def test_frigate_ground_hint_person_absent(cam):
    assert cam._frigate_ground_hint(None) == ""
    hint = cam._frigate_ground_hint({"person": False, "car": True})
    assert "NO person" in hint and "car" in hint
    assert "person" in cam._frigate_ground_hint({"person": True})
    assert "car" in cam._frigate_ground_hint({"car": True})


def test_lowlight_hint_present(cam):
    assert "infrared" in cam._LOWLIGHT_HINT and "shadows" in cam._LOWLIGHT_HINT


# ── Announce gating: important-only (v7.93.0) ────────────────────────────────
def _j(notable, category, speak="Someone is at the door."):
    return {"notable": notable, "category": category, "speak": speak, "summary": "x"}


def test_auto_important_only_mutes_mundane(cam):
    # Auto review, important-only ON: mundane categories stay silent even if the
    # (weak) reasoning model flagged them notable.
    for cat in ("vehicle", "animal", "empty", "known_resident", "other"):
        assert cam._announce_decision(_j(True, cat), "desc",
                                      gate_announce=True, important_only=True) is None


def test_auto_important_only_announces_important(cam):
    for cat in ("person", "delivery", "package", "mail"):
        assert cam._announce_decision(_j(True, cat), "desc",
                                      gate_announce=True, important_only=True) == "Someone is at the door."


def test_important_only_off_announces_any_notable(cam):
    # Verbose mode: a notable vehicle speaks.
    assert cam._announce_decision(_j(True, "vehicle"), "desc",
                                  gate_announce=True, important_only=False) == "Someone is at the door."


def test_manual_request_bypasses_mute(cam):
    # Manual analyze (gate_announce=False): user asked, so even a mundane category
    # is reported, and a non-notable scene reports the full description.
    assert cam._announce_decision(_j(True, "vehicle"), "desc",
                                  gate_announce=False, important_only=True) == "Someone is at the door."
    assert cam._announce_decision(_j(False, "empty"), "full analysis",
                                  gate_announce=False, important_only=True) == "full analysis"


def test_auto_not_notable_is_silent(cam):
    assert cam._announce_decision(_j(False, "empty"), "desc",
                                  gate_announce=True, important_only=True) is None
    # notable but no speak text → nothing to say
    assert cam._announce_decision(_j(True, "person", speak=""), "desc",
                                  gate_announce=True, important_only=True) is None


def test_mute_set_membership(cam):
    assert {"vehicle", "animal", "empty", "known_resident", "other"} <= cam._MUTE_CATEGORIES
    for keep in ("person", "delivery", "package", "mail"):
        assert keep not in cam._MUTE_CATEGORIES
