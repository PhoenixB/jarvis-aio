"""Tests for mmWave distance ingestion + proximity TTS volume dampening.

Pins the continuous damping ramp (near→quiet, far→full, unknown→no-op) and the
distance-array reader that feeds it, including unit normalisation to metres and
Euclidean distance from ``*_presence_coordinates``.
"""
import importlib.util
import pathlib
import sys

from fakes import FakeHass

COMP = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"
AREA = "office"


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, COMP / relpath)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


spatial = _load("jarvis_spatial_prox", "vision/spatial.py")
damp = spatial.volume_damping_factor


# ── damping ramp ──────────────────────────────────────────────────────────────
def test_unknown_distance_is_noop():
    assert damp(None) == 1.0
    assert damp("not a number") == 1.0
    assert damp(float("nan")) == 1.0
    assert damp(-3.0) == 1.0


def test_near_is_floor_far_is_full():
    assert damp(0.5, near_m=1.0, far_m=6.0, floor=0.55) == 0.55
    assert damp(1.0, near_m=1.0, far_m=6.0, floor=0.55) == 0.55
    assert damp(6.0, near_m=1.0, far_m=6.0, floor=0.55) == 1.0
    assert damp(9.0, near_m=1.0, far_m=6.0, floor=0.55) == 1.0


def test_midpoint_is_between_floor_and_one():
    mid = damp(3.5, near_m=1.0, far_m=6.0, floor=0.55)   # halfway
    assert 0.55 < mid < 1.0
    assert abs(mid - 0.775) < 1e-6


def test_monotonic_increasing_with_distance():
    vals = [damp(d, near_m=1.0, far_m=6.0, floor=0.5) for d in (1, 2, 3, 4, 5, 6)]
    assert vals == sorted(vals)


def test_degenerate_range_is_clean_step():
    # near >= far collapses to a near/far step, never a divide-by-zero
    assert damp(2.0, near_m=5.0, far_m=5.0, floor=0.4) == 0.4
    assert damp(6.0, near_m=5.0, far_m=5.0, floor=0.4) == 1.0


# ── distance reader ──────────────────────────────────────────────────────────
def _engine(states):
    hass = FakeHass()
    for eid, (state, attrs) in states.items():
        hass.states.set(eid, state, **attrs)
    return spatial.SpatialContextEngine(hass)


def test_nearest_distance_none_when_no_sensors():
    e = _engine({f"binary_sensor.{AREA}_mmwave_presence": ("on", {})})
    assert e.nearest_distance_m(AREA) is None


def test_nearest_distance_picks_minimum_in_metres():
    e = _engine({
        f"sensor.{AREA}_target_distance": ("2.5", {}),
        f"sensor.{AREA}_moving_target_distance": ("0.9", {}),
    })
    assert e.nearest_distance_m(AREA) == 0.9


def test_distance_unit_mm_normalised_to_metres():
    e = _engine({
        f"sensor.{AREA}_distance": ("1500", {"unit_of_measurement": "mm"}),
    })
    assert e.nearest_distance_m(AREA) == 1.5


def test_distance_unit_cm_normalised_to_metres():
    e = _engine({
        f"sensor.{AREA}_distance": ("120", {"unit_of_measurement": "cm"}),
    })
    assert e.nearest_distance_m(AREA) == 1.2


def test_presence_coordinates_euclidean_distance():
    e = _engine({
        f"sensor.{AREA}_presence_coordinates": ("3,4", {}),   # → 5.0
    })
    assert e.nearest_distance_m(AREA) == 5.0


def test_other_area_sensors_are_ignored():
    e = _engine({
        f"sensor.kitchen_distance": ("0.3", {}),
        f"sensor.{AREA}_distance": ("4.0", {}),
    })
    assert e.nearest_distance_m(AREA) == 4.0


def test_unreadable_distance_state_is_skipped():
    e = _engine({
        f"sensor.{AREA}_distance": ("unavailable", {}),
        f"sensor.{AREA}_moving_target_distance": ("2.0", {}),
    })
    assert e.nearest_distance_m(AREA) == 2.0
