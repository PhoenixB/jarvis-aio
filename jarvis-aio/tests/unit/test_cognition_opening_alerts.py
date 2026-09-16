"""Door/window/garage anticipation suppression (v7.90.0).

`cognition.predict()` speaks "X is open — around this time it's usually closed".
The new `door_window_alerts_enabled` flag must silence that for opening-class
entities (door/window/garage/opening) WITHOUT removing them from awareness, while
leaving other occupancy anticipations (e.g. a lock) untouched.

Pure logic: cognition.py imports nothing from Home Assistant, and reads the flag
via `jarvis_config` (a pure module). Both load under the synthetic `jc` package.
"""
import importlib
import importlib.util
import sys
import time
import types
from pathlib import Path

import pytest

from fakes import FakeHass

COMP = Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


@pytest.fixture
def cog():
    if "jc" not in sys.modules:
        pkg = types.ModuleType("jc")
        pkg.__path__ = [str(COMP)]
        sys.modules["jc"] = pkg
    # Clean, known config baseline for every test (no disk read, no leakage
    # between tests). cognition reads the flag off this same module.
    jcfg = importlib.import_module("jc.jarvis_config")
    jcfg._loaded = True
    jcfg._cache = {}
    # Fresh cognition module state (module globals: _MODEL, cooldowns).
    sys.modules.pop("jc.cognition", None)
    spec = importlib.util.spec_from_file_location("jc.cognition", COMP / "cognition.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jc.cognition"] = mod
    spec.loader.exec_module(mod)
    return mod


def _set_cfg(**kw):
    """Force jarvis_config values without touching disk."""
    jcfg = importlib.import_module("jc.jarvis_config")
    jcfg._loaded = True
    jcfg._cache = dict(kw)


def _prime(cog, hass, eid, dcls, cur, dominant, now, name):
    """Seed the occupancy model + live state so predict() would fire for eid:
    current state is rare for this hour and has been held past the stillness gate."""
    hour = time.localtime(now).tm_hour
    e = cog._Entry(now)
    e.occ = {hour: {dominant: 30, cur: 1}}          # cur share 1/31 < 0.20 (unusual)
    e.last_changed = now - (cog.OCC_MIN_STILLNESS + 100)  # past the transient gate
    cog._MODEL[eid] = e
    hass.states.set(eid, cur, device_class=dcls, friendly_name=name)


def test_opening_alert_emitted_by_default(cog):
    now = time.time()
    hass = FakeHass()
    _prime(cog, hass, "binary_sensor.kitchen_window", "window", "on", "off", now, "Kitchen Window")
    preds = cog.predict(hass, now)
    assert len(preds) == 1
    p = preds[0]
    assert p["type"] == "anticipation"
    assert "Kitchen Window is open" in p["message"]
    assert "usually closed" in p["message"]


def test_opening_alert_suppressed_when_disabled(cog):
    _set_cfg(door_window_alerts_enabled=False)
    now = time.time()
    hass = FakeHass()
    _prime(cog, hass, "binary_sensor.kitchen_window", "window", "on", "off", now, "Kitchen Window")
    _prime(cog, hass, "binary_sensor.garage_1", "garage_door", "on", "off", now, "Garage")
    _prime(cog, hass, "binary_sensor.back_door", "door", "on", "off", now, "Back Door")
    assert cog.predict(hass, now) == []


def test_non_opening_anticipation_still_fires_when_disabled(cog):
    # A lock-class entity is NOT an opening class, so the flag must not gate it.
    _set_cfg(door_window_alerts_enabled=False)
    now = time.time()
    hass = FakeHass()
    _prime(cog, hass, "binary_sensor.front_lock", "lock", "unlocked", "locked", now, "Front Lock")
    preds = cog.predict(hass, now)
    assert len(preds) == 1
    assert "Front Lock" in preds[0]["message"]


def test_default_flag_true(cog):
    assert cog._opening_alerts_enabled() is True
    assert "window" in cog._OPENING_CLASSES and "garage_door" in cog._OPENING_CLASSES
    assert "lock" not in cog._OPENING_CLASSES
