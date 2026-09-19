#!/usr/bin/env python3
"""Convert a SweetHome3D JSON export into JARVIS `floor_plan_rooms` JSON.

The Residence tab stores its plan under the `floor_plan_rooms` config key as

    {"<floor>": {"rooms": [{"name", "x", "y", "w", "h"}, ...], "labels": [...]}}

(see `custom_components/jarvis/residence_graph.py`, which reads it to derive
room adjacency). SweetHome3D, by contrast, describes a home as a set of wall
segments, doors/windows, furniture and — when you draw them — `room` polygons.
This helper reads a SweetHome3D JSON export and emits the axis-aligned bounding
box of every room polygon in the shape JARVIS expects, so you can paste the
result straight into the config instead of hand-escaping JSON.

Usage
-----
    # From a JSON file, pretty plan to stdout:
    python3 scripts/sweethome3d_to_floorplan.py home.json

    # From stdin, and also a paste-ready escaped string for the config field:
    cat home.json | python3 scripts/sweethome3d_to_floorplan.py - --as-config-string

    # Scale SweetHome3D centimetres down and shift the plan to a (0,0) origin:
    python3 scripts/sweethome3d_to_floorplan.py home.json --scale 0.5 --origin-zero

Notes
-----
* SweetHome3D uses centimetres with y increasing downward, the same axis
  convention JARVIS's plan uses, so no axis flip is needed. `--scale` just
  rescales the numbers; adjacency is scale-independent (the touch test uses a
  gap proportional to the coordinates).
* Rooms are grouped by SweetHome3D level (floor) when the export defines
  levels; otherwise everything lands on a single floor (`--floor`, default
  "main").
* If the export contains no `room` polygons — a SweetHome3D file can be all
  walls and furniture with no rooms drawn — there is nothing to convert. Draw
  rooms in SweetHome3D first (Plan menu -> Create rooms, or double-click inside
  a closed set of walls to auto-detect one), re-export, and run this again.

Exit code 0 = at least one room converted, 1 = nothing to convert / bad input.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional


def _load(source: str) -> Any:
    text = sys.stdin.read() if source == "-" else open(source, encoding="utf-8").read()
    return json.loads(text)


def _home(doc: Any) -> dict:
    """Unwrap the SweetHome3D home object from common export shapes."""
    if isinstance(doc, dict):
        if isinstance(doc.get("home"), dict):
            return doc["home"]
        return doc
    raise ValueError("expected a JSON object at the top level")


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _points(room: dict) -> list[tuple[float, float]]:
    """Pull [x, y] vertices from a SweetHome3D room, tolerant of shapes."""
    raw = room.get("points")
    if raw is None:
        raw = room.get("sPoints") or room.get("point")
    pts: list[tuple[float, float]] = []
    for p in _as_list(raw):
        try:
            if isinstance(p, dict):
                pts.append((float(p["x"]), float(p["y"])))
            else:  # [x, y] pair
                pts.append((float(p[0]), float(p[1])))
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return pts


def _level_names(home: dict) -> dict[str, str]:
    """{level_id: level_name} for grouping rooms onto floors."""
    names: dict[str, str] = {}
    for i, lvl in enumerate(_as_list(home.get("level"))):
        if not isinstance(lvl, dict):
            continue
        lid = str(lvl.get("id", i))
        names[lid] = str(lvl.get("name") or lid).strip() or lid
    return names


def convert(home: dict, *, scale: float, default_floor: str) -> dict[str, dict]:
    level_names = _level_names(home)
    plan: dict[str, dict] = {}
    auto = 0
    for room in _as_list(home.get("room") or home.get("rooms")):
        if not isinstance(room, dict):
            continue
        pts = _points(room)
        if len(pts) < 3:
            continue  # not a polygon
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, y0 = min(xs), min(ys)
        auto += 1
        name = str(room.get("name") or "").strip() or f"Room {auto}"
        floor = level_names.get(str(room.get("level")), default_floor)
        box = {
            "name": name,
            "x": round(x0 * scale, 2),
            "y": round(y0 * scale, 2),
            "w": round((max(xs) - x0) * scale, 2),
            "h": round((max(ys) - y0) * scale, 2),
        }
        plan.setdefault(floor, {"rooms": [], "labels": []})["rooms"].append(box)
    return plan


def _shift_to_origin(plan: dict[str, dict]) -> None:
    """Translate all rooms so the top-left of the whole plan sits at (0, 0)."""
    boxes = [r for f in plan.values() for r in f["rooms"]]
    if not boxes:
        return
    dx = min(r["x"] for r in boxes)
    dy = min(r["y"] for r in boxes)
    for r in boxes:
        r["x"] = round(r["x"] - dx, 2)
        r["y"] = round(r["y"] - dy, 2)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", help="SweetHome3D JSON export file, or - for stdin")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="multiply every coordinate (default 1.0; SweetHome3D units are cm)")
    ap.add_argument("--floor", default="main",
                    help="floor name for rooms with no SweetHome3D level (default: main)")
    ap.add_argument("--origin-zero", action="store_true",
                    help="translate the plan so its top-left corner is (0, 0)")
    ap.add_argument("--as-config-string", action="store_true",
                    help="also print the escaped one-line JSON string to paste into floor_plan_rooms")
    args = ap.parse_args(argv)

    try:
        home = _home(_load(args.source))
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"error: could not read SweetHome3D JSON: {e}", file=sys.stderr)
        return 1

    plan = convert(home, scale=args.scale, default_floor=args.floor)
    if args.origin_zero:
        _shift_to_origin(plan)

    total = sum(len(f["rooms"]) for f in plan.values())
    if not total:
        print(
            "error: no room polygons found in this SweetHome3D export.\n"
            "A file that is only walls, doors and furniture has no rooms to import.\n"
            "In SweetHome3D, draw rooms first (Plan menu -> Create rooms, or\n"
            "double-click inside a closed set of walls to auto-detect one),\n"
            "re-export to JSON, and run this again.",
            file=sys.stderr,
        )
        return 1

    print(json.dumps(plan, indent=2, ensure_ascii=False))
    if args.as_config_string:
        print("\n# Paste this value into the floor_plan_rooms config field:", file=sys.stderr)
        print(json.dumps(json.dumps(plan, ensure_ascii=False)))
    print(f"# converted {total} room(s) across {len(plan)} floor(s)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
