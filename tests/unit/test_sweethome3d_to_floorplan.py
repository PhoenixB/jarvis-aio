"""Tests for scripts/sweethome3d_to_floorplan.py — SweetHome3D -> floor_plan_rooms."""
import importlib.util
import json
import pathlib

import pytest

_SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "sweethome3d_to_floorplan.py"


@pytest.fixture(scope="module")
def conv():
    spec = importlib.util.spec_from_file_location("sh3d_conv", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rooms_become_bounding_boxes(conv):
    home = {"room": [
        {"name": "Living Room", "points": [[0, 0], [400, 0], [400, 300], [0, 300]]},
        {"name": "Kitchen", "points": [[400, 0], [700, 0], [700, 300], [400, 300]]},
    ]}
    plan = conv.convert(home, scale=1.0, default_floor="main")
    rooms = {r["name"]: r for r in plan["main"]["rooms"]}
    assert rooms["Living Room"] == {"name": "Living Room", "x": 0, "y": 0, "w": 400, "h": 300}
    assert rooms["Kitchen"]["x"] == 400 and rooms["Kitchen"]["w"] == 300


def test_home_wrapper_and_levels(conv):
    doc = {"home": {
        "level": [{"id": "lvl0", "name": "Ground"}, {"id": "lvl1", "name": "Upstairs"}],
        "room": [
            {"name": "Hall", "level": "lvl0", "points": [[0, 0], [10, 0], [10, 10], [0, 10]]},
            {"name": "Bed", "level": "lvl1", "points": [[0, 0], [20, 0], [20, 20], [0, 20]]},
        ],
    }}
    plan = conv.convert(conv._home(doc), scale=1.0, default_floor="main")
    assert set(plan) == {"Ground", "Upstairs"}
    assert plan["Ground"]["rooms"][0]["name"] == "Hall"
    assert plan["Upstairs"]["rooms"][0]["name"] == "Bed"


def test_scale_and_origin_zero(conv):
    home = {"room": [{"name": "R", "points": [[200, 100], [400, 100], [400, 300], [200, 300]]}]}
    plan = conv.convert(home, scale=0.5, default_floor="main")
    conv._shift_to_origin(plan)
    r = plan["main"]["rooms"][0]
    assert (r["x"], r["y"], r["w"], r["h"]) == (0, 0, 100, 100)


def test_dict_points_and_auto_names(conv):
    home = {"room": [{"points": [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 5}]}]}
    plan = conv.convert(home, scale=1.0, default_floor="main")
    assert plan["main"]["rooms"][0]["name"] == "Room 1"


def test_walls_only_export_yields_no_rooms(conv):
    # The shape reported in issue #34: walls/doors/furniture but no room polygons.
    home = {"wall": [{"xStart": 0, "yStart": 0, "xEnd": 100, "yEnd": 0}],
            "doorOrWindow": [{"id": "d1"}], "pieceOfFurniture": [{"id": "f1"}]}
    assert conv.convert(home, scale=1.0, default_floor="main") == {}


def test_degenerate_polygons_skipped(conv):
    home = {"room": [
        {"name": "line", "points": [[0, 0], [10, 0]]},   # < 3 points
        {"name": "ok", "points": [[0, 0], [1, 0], [1, 1]]},
    ]}
    plan = conv.convert(home, scale=1.0, default_floor="main")
    names = [r["name"] for r in plan["main"]["rooms"]]
    assert names == ["ok"]


def test_output_matches_floor_plan_rooms_schema(conv):
    """Bounding boxes must have exactly the keys residence_graph._boxes reads."""
    home = {"room": [{"name": "X", "points": [[0, 0], [1, 0], [1, 1], [0, 1]]}]}
    plan = conv.convert(home, scale=1.0, default_floor="main")
    box = plan["main"]["rooms"][0]
    assert set(box) == {"name", "x", "y", "w", "h"}
    assert plan["main"]["labels"] == []
    # round-trips through JSON string exactly as the config stores it
    assert json.loads(json.dumps(plan)) == plan
