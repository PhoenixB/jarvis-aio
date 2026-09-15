"""Driving Mode (v7.89.0) — routing decisions + car_ui payload augmentation.

Pure logic, so it runs without a Home Assistant runtime: the module reads only
the resolved config dict and hass.states, both faked. Loaded under the synthetic
`jc` package (conftest) so its `from .const import ...` resolves.
"""
import importlib.util
import sys
from pathlib import Path

import pytest

from fakes import FakeHass

COMP = Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


@pytest.fixture
def dm():
    key = "jc.driving_mode"
    if key in sys.modules:
        del sys.modules[key]
    spec = importlib.util.spec_from_file_location(key, COMP / "driving_mode.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)
    return mod


def _hass_with_car(state="on", eid="binary_sensor.pixel_9_pro_android_auto"):
    h = FakeHass()
    h.states.set(eid, state, friendly_name="Pixel 9 Pro Android Auto")
    return h


# ── category mapping ─────────────────────────────────────────────────────────
def test_category_for_maps_known_kinds(dm):
    assert dm.category_for("anticipation_departure") == dm.CAT_TRAVEL
    assert dm.category_for("intrusion_confirmed") == dm.CAT_SECURITY
    assert dm.category_for("freeze_critical") == dm.CAT_SECURITY
    assert dm.category_for("scheduled") == dm.CAT_BRIEFING
    assert dm.category_for("camera") == dm.CAT_SECURITY
    # prefix fallback for un-enumerated members of a family
    assert dm.category_for("hazard_wildfire") == dm.CAT_SECURITY
    assert dm.category_for("departure_soon") == dm.CAT_TRAVEL
    # unknown → the catch-all, which never routes
    assert dm.category_for("some_new_thing") == dm.CAT_GENERAL
    assert dm.category_for("") == dm.CAT_GENERAL


# ── gating ───────────────────────────────────────────────────────────────────
def test_no_route_when_disabled(dm):
    hass = _hass_with_car()
    cfg = {"driving_mode_enabled": False, "driving_notify_travel": True}
    assert dm.should_route(hass, cfg, dm.CAT_TRAVEL) is False


def test_no_route_when_car_not_connected(dm):
    hass = _hass_with_car(state="off")
    cfg = {"driving_mode_enabled": True, "driving_notify_travel": True}
    assert dm.should_route(hass, cfg, dm.CAT_TRAVEL) is False


def test_no_route_when_category_off(dm):
    hass = _hass_with_car()
    cfg = {"driving_mode_enabled": True, "driving_notify_travel": False}
    assert dm.should_route(hass, cfg, dm.CAT_TRAVEL) is False


def test_general_category_never_routes(dm):
    hass = _hass_with_car()
    cfg = {"driving_mode_enabled": True}  # categories default on
    assert dm.should_route(hass, cfg, dm.CAT_GENERAL) is False


def test_route_when_enabled_connected_and_category_on(dm):
    hass = _hass_with_car()
    cfg = {"driving_mode_enabled": True}  # travel defaults on
    assert dm.should_route(hass, cfg, dm.CAT_TRAVEL) is True


# ── car-connection detection ─────────────────────────────────────────────────
def test_auto_discovers_android_auto_sensor(dm):
    hass = _hass_with_car()  # no sensor configured
    cfg = {"driving_mode_enabled": True}
    assert dm.is_car_connected(hass, cfg) is True
    assert any(e.endswith("_android_auto") for e in dm.car_sensors(hass, cfg))


def test_configured_sensor_wins_over_autodetect(dm):
    hass = FakeHass()
    hass.states.set("binary_sensor.car_link", "on", friendly_name="Car Link")
    cfg = {"driving_mode_enabled": True, "driving_car_sensor": "binary_sensor.car_link"}
    assert dm.car_sensors(hass, cfg) == ["binary_sensor.car_link"]
    assert dm.is_car_connected(hass, cfg) is True


def test_various_connected_state_strings(dm):
    for on_state in ("on", "connected", "home", "true"):
        hass = _hass_with_car(state=on_state)
        assert dm.is_car_connected(hass, {"driving_mode_enabled": True}) is True


# ── payload augmentation ─────────────────────────────────────────────────────
def test_augment_adds_car_ui_and_keeps_image(dm):
    hass = _hass_with_car()
    cfg = {"driving_mode_enabled": True}
    base = {"image": "/local/x.jpg", "attachment": {"url": "/local/x.jpg"}}
    out = dm.augment_data(hass, cfg, "anticipation_departure", base)
    assert out["car_ui"] is True
    assert out["importance"] == "high"
    assert out["channel"] == dm.DRIVING_CHANNEL
    # existing image keys preserved, input not mutated
    assert out["image"] == "/local/x.jpg"
    assert "car_ui" not in base


def test_augment_noop_when_not_routing(dm):
    hass = _hass_with_car(state="off")  # not connected
    cfg = {"driving_mode_enabled": True}
    base = {"image": "/local/x.jpg"}
    out = dm.augment_data(hass, cfg, "anticipation_departure", base)
    assert "car_ui" not in out
    assert out == base


def test_augment_general_kind_never_routes(dm):
    hass = _hass_with_car()
    cfg = {"driving_mode_enabled": True}
    out = dm.augment_data(hass, cfg, "some_unmapped_kind", None)
    assert out == {}


# ── suppress-home default ────────────────────────────────────────────────────
def test_suppress_home_audio_default_on(dm):
    assert dm.suppress_home_audio({}) is True
    assert dm.suppress_home_audio({"driving_suppress_home_audio": False}) is False
