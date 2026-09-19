"""Spatial context fusion for JARVIS.

``SpatialContextEngine`` fuses three per-area presence signals into a single
occupancy-confidence score and decides whether an announcement can drop its
preamble (speak straight to the point because the user is demonstrably present
and looking):

    sensor.{area}_frigate_person_count        > 0   → +0.60
    binary_sensor.{area}_camera_gaze_detected  on   → +0.20
    binary_sensor.{area}_mmwave_presence       on   → +0.35

Confidence is clamped to [0.0, 1.0]. When gaze AND mmWave presence are both
established, ``skip_preamble`` is True.

Reads the Home Assistant state machine but imports no HA modules, so it stays a
pure, directly-testable leaf.

It also reads high-resolution mmWave *distance* arrays (``sensor.{area}_distance``
/ ``sensor.{area}_presence_coordinates``) to estimate how far the listener is
from the room's speaker, which the proactive-audio pipeline uses to dampen TTS
volume when someone is right next to the satellite instead of blaring at a fixed
level. See ``volume_damping_factor``.
"""
from __future__ import annotations

import logging
from typing import Optional

_LOGGER = logging.getLogger(__name__)

_ON_STATES = {"on", "true", "detected", "home", "occupied"}

# Distance-sensor unit handling. mmWave radars (LD2410/LD2450 and friends) report
# in mm, cm, or m depending on firmware; we normalise everything to metres using
# the entity's declared unit, defaulting to metres when it says nothing.
_DIST_TO_M = {"mm": 0.001, "cm": 0.01, "m": 1.0}

# Damping ramp defaults (metres). At/below NEAR the listener is on top of the
# speaker → quietest; at/beyond FAR → full computed volume; linear between.
_PROX_NEAR_M = 1.0
_PROX_FAR_M = 6.0
_PROX_FLOOR = 0.55        # never dampen below this fraction of the intended volume


def volume_damping_factor(
    distance_m: Optional[float],
    *,
    near_m: float = _PROX_NEAR_M,
    far_m: float = _PROX_FAR_M,
    floor: float = _PROX_FLOOR,
) -> float:
    """Continuous proximity → volume multiplier in ``[floor, 1.0]``.

    Near the speaker JARVIS speaks softly; far away it uses the full computed
    volume. Unknown distance (no radar, unreadable value) returns ``1.0`` so the
    behaviour is unchanged wherever distance can't be measured — the feature is
    strictly additive. Never raises; degenerate ``near>=far`` ranges collapse to
    a clean near/far step."""
    if distance_m is None:
        return 1.0
    try:
        d = float(distance_m)
    except (TypeError, ValueError):
        return 1.0
    if d != d or d < 0:                      # NaN / nonsensical
        return 1.0
    floor = max(0.0, min(1.0, floor))
    if far_m <= near_m:
        return 1.0 if d >= far_m else floor
    if d <= near_m:
        return floor
    if d >= far_m:
        return 1.0
    ramp = (d - near_m) / (far_m - near_m)   # 0 at near … 1 at far
    return round(floor + (1.0 - floor) * ramp, 3)


class SpatialContextEngine:
    """Fuse Frigate person-object, camera-gaze, and mmWave presence per area."""

    PERSON_COUNT_WEIGHT = 0.60
    GAZE_WEIGHT = 0.20
    PRESENCE_WEIGHT = 0.35

    CONFIDENCE_MIN = 0.0
    CONFIDENCE_MAX = 1.0

    def __init__(self, hass) -> None:
        self.hass = hass

    # ── Signal readers (each fully guarded) ───────────────────────────────
    def _person_count(self, area_id: str) -> int:
        eid = f"sensor.{area_id}_frigate_person_count"
        state = self.hass.states.get(eid)
        if state is None:
            return 0
        try:
            return max(0, int(float(state.state)))
        except (TypeError, ValueError):
            return 0

    def _binary_on(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        if state is None:
            return False
        return str(state.state).lower() in _ON_STATES

    # ── Distance / proximity ──────────────────────────────────────────────
    @staticmethod
    def _coordinate_distance(raw: str) -> Optional[float]:
        """Euclidean distance from an ``x,y`` (or ``x,y,z``) coordinate string,
        as radars publishing ``*_presence_coordinates`` emit. Assumes the same
        unit as plain distance sensors (normalised by the caller)."""
        try:
            nums = [float(p) for p in str(raw).replace(";", ",").split(",") if p.strip() != ""]
        except (TypeError, ValueError):
            return None
        if not nums:
            return None
        return sum(n * n for n in nums) ** 0.5

    def nearest_distance_m(self, area_id: str) -> Optional[float]:
        """Closest tracked target distance in ``area_id``, in metres, or None.

        Scans this area's ``sensor.{area}_*distance`` and
        ``sensor.{area}_*presence_coordinates`` entities, normalises each to
        metres by its declared unit, and returns the minimum. None when the area
        has no readable distance array — the common case, which the caller treats
        as 'unknown, change nothing'."""
        prefix = f"sensor.{area_id}_"
        best: Optional[float] = None
        try:
            states = self.hass.states.async_all("sensor")
        except Exception:
            return None
        for st in states:
            eid = getattr(st, "entity_id", "")
            if not eid.startswith(prefix):
                continue
            is_coord = eid.endswith("_presence_coordinates") or eid.endswith("_coordinates")
            is_dist = eid.endswith("_distance") or eid.endswith("distance")
            if not (is_coord or is_dist):
                continue
            attrs = getattr(st, "attributes", {}) or {}
            unit = str(attrs.get("unit_of_measurement", "") or "").strip().lower()
            factor = _DIST_TO_M.get(unit, 1.0)
            if is_coord:
                native = self._coordinate_distance(getattr(st, "state", ""))
            else:
                try:
                    native = float(getattr(st, "state", ""))
                except (TypeError, ValueError):
                    native = None
            if native is None or native < 0:
                continue
            metres = native * factor
            if best is None or metres < best:
                best = metres
        return round(best, 2) if best is not None else None

    # ── Fusion ────────────────────────────────────────────────────────────
    def evaluate(self, area_id: str) -> dict:
        """Return the fused spatial context for ``area_id``.

        Keys:
            confidence    (float, 0.0–1.0) – fused occupancy confidence
            skip_preamble (bool)           – gaze AND presence established
            person_count  (int)            – Frigate person objects in view
            gaze          (bool)           – camera gaze detected
            presence      (bool)           – mmWave presence detected
        """
        person_count = self._person_count(area_id)
        gaze = self._binary_on(f"binary_sensor.{area_id}_camera_gaze_detected")
        presence = self._binary_on(f"binary_sensor.{area_id}_mmwave_presence")

        confidence = 0.0
        if person_count > 0:
            confidence += self.PERSON_COUNT_WEIGHT
        if gaze:
            confidence += self.GAZE_WEIGHT
        if presence:
            confidence += self.PRESENCE_WEIGHT
        confidence = max(self.CONFIDENCE_MIN, min(self.CONFIDENCE_MAX, confidence))

        skip_preamble = bool(gaze and presence)

        _LOGGER.debug(
            "spatial[%s]: persons=%d gaze=%s presence=%s → conf=%.2f skip_preamble=%s",
            area_id, person_count, gaze, presence, confidence, skip_preamble,
        )
        return {
            "confidence": round(confidence, 2),
            "skip_preamble": skip_preamble,
            "person_count": person_count,
            "gaze": gaze,
            "presence": presence,
        }
