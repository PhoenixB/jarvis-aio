# Importing a floor plan (`floor_plan_rooms`)

The **Residence** tab draws its plan from the `floor_plan_rooms` config key. JARVIS
uses that plan for more than decoration: `residence_graph.py` derives room
**adjacency** from it so the intrusion investigator can follow a plausible route
inward from a breach point instead of treating every motion zone as equivalent.

## The format JARVIS expects

`floor_plan_rooms` is a map of **floor name → rooms**, where each room is an
axis-aligned box:

```json
{
  "1F": {
    "rooms": [
      { "name": "Living Room", "x": 0,   "y": 0, "w": 400, "h": 300 },
      { "name": "Kitchen",     "x": 400, "y": 0, "w": 300, "h": 300 }
    ],
    "labels": []
  }
}
```

* `x`, `y` are the box's top-left corner; `w`, `h` its width and height. The y
  axis increases **downward** (screen coordinates).
* `name` should match the Home Assistant **area** name (case/spacing-insensitive)
  so motion, which JARVIS knows by area, can be located on the plan.
* Two rooms are treated as adjacent when their boxes touch or nearly touch, so
  keep neighbouring rooms sharing an edge.

### Object vs. stringified string

You do **not** need to double-escape anything. JARVIS accepts `floor_plan_rooms`
as **either** a JSON object **or** a JSON string — `residence_graph._boxes()` and
the panel both `json.loads()` a string and otherwise use the object as-is. The
escaping pain only appears when you hand-edit Home Assistant's stored config,
where the value happens to be persisted as a string. Editing the plan on the
**Residence** tab avoids the escaping entirely.

## Importing from SweetHome3D

`scripts/sweethome3d_to_floorplan.py` converts a SweetHome3D **JSON export** into
the format above. SweetHome3D describes a home as walls, doors/windows, furniture
and — **when you draw them** — `room` polygons. The converter turns each room
polygon into its bounding box, grouped by SweetHome3D level (floor).

```bash
# Pretty plan to stdout:
python3 scripts/sweethome3d_to_floorplan.py home.json

# Also print a paste-ready, escaped one-line string for the config field:
python3 scripts/sweethome3d_to_floorplan.py home.json --as-config-string

# SweetHome3D units are centimetres; scale down and re-origin to (0,0):
python3 scripts/sweethome3d_to_floorplan.py home.json --scale 0.5 --origin-zero
```

Then paste the JSON onto the Residence tab (or into the `floor_plan_rooms`
config field).

### "No room polygons found"

If the converter reports that no rooms were found, your SweetHome3D file is
**all walls, doors and furniture with no rooms drawn** — there is nothing to
import as rooms. In SweetHome3D, create rooms first:

* **Plan → Create rooms**, then click each corner, or
* double-click inside a closed set of walls to auto-detect a room.

Re-export to JSON and run the converter again.
