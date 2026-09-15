"""JARVIS — Driving Mode (v7.89.0).

When the phone is projecting to the car (Android Auto), JARVIS's proactive
heads-ups are useless spoken to an empty house — and the car head unit is a
closed projection sink, so the only way onto its screen is a mobile-app
notification the companion app renders in the Android Auto UI (``car_ui: true``).

This module is the single decision point for that behaviour. It answers three
questions for any outgoing proactive notification:

  1. Is Driving Mode on at all?                         :func:`enabled`
  2. Is this *kind* of alert one the user wants routed?  per-category flags
  3. Is the car actually connected right now?            :func:`is_car_connected`

Only when all three hold does :func:`augment_data` fold the ``car_ui`` fields
into the notification's ``data`` block. Everything is driven by config keys and
live entity state — no hard-coded entity IDs, no assumption about the user's
phone or car — so it behaves for any household, not just one deployment.

Pure module: it takes ``hass`` and the resolved ``config`` dict as parameters
and reads only ``hass.states``. No Home Assistant import at load time, so it is
trivially unit-testable in isolation.
"""
from __future__ import annotations

import json
import logging

from .const import (
    CONF_DRIVING_MODE_ENABLED,
    CONF_DRIVING_CAR_SENSOR,
    CONF_DRIVING_NOTIFY_BRIEFINGS,
    CONF_DRIVING_NOTIFY_TRAVEL,
    CONF_DRIVING_NOTIFY_SECURITY,
    CONF_DRIVING_SUPPRESS_HOME,
    DEFAULT_DRIVING_MODE_ENABLED,
    DEFAULT_DRIVING_NOTIFY_BRIEFINGS,
    DEFAULT_DRIVING_NOTIFY_TRAVEL,
    DEFAULT_DRIVING_NOTIFY_SECURITY,
    DEFAULT_DRIVING_SUPPRESS_HOME,
    DRIVING_CHANNEL,
)

_LOGGER = logging.getLogger(__name__)

# ── Categories ───────────────────────────────────────────────────────────────
# Internal notification "kinds" collapse to three user-facing categories the
# panel exposes, plus a catch-all that is never routed (so nothing surprising
# ends up on the car screen without an explicit opt-in).
CAT_BRIEFING = "briefing"
CAT_TRAVEL = "travel"
CAT_SECURITY = "security"
CAT_GENERAL = "general"

# Exact action_type / reason → category. Anything not listed falls through to the
# prefix rules below, then to CAT_GENERAL.
_EXACT: dict[str, str] = {
    # Leave-time / travel anticipation
    "anticipation_departure": CAT_TRAVEL,
    # Proactive briefings (proactive_briefing reasons + scheduled digests)
    "briefing": CAT_BRIEFING,
    "scheduled": CAT_BRIEFING,
    "arrival": CAT_BRIEFING,
    # Security / safety / hazard family
    "security": CAT_SECURITY,
    "camera": CAT_SECURITY,
    "doorbell": CAT_SECURITY,
    "recognition": CAT_SECURITY,
    "intrusion_confirmed": CAT_SECURITY,
    "intrusion_investigating": CAT_SECURITY,
    "intrusion_alert": CAT_SECURITY,
    "intrusion_away": CAT_SECURITY,
    "intrusion_sleep": CAT_SECURITY,
    "lockdown": CAT_SECURITY,
    "freeze_critical": CAT_SECURITY,
    "freeze_warning": CAT_SECURITY,
}

# Prefix fallbacks — keeps new action_type strings in an existing family mapped
# correctly without needing an exact entry each time.
_PREFIX: tuple[tuple[str, str], ...] = (
    ("anticipation_departure", CAT_TRAVEL),
    ("departure", CAT_TRAVEL),
    ("travel", CAT_TRAVEL),
    ("leave", CAT_TRAVEL),
    ("briefing", CAT_BRIEFING),
    ("digest", CAT_BRIEFING),
    ("morning", CAT_BRIEFING),
    ("intrusion", CAT_SECURITY),
    ("lockdown", CAT_SECURITY),
    ("freeze", CAT_SECURITY),
    ("hazard", CAT_SECURITY),
    ("quake", CAT_SECURITY),
    ("earthquake", CAT_SECURITY),
    ("weather", CAT_SECURITY),
    ("disaster", CAT_SECURITY),
    ("anomaly", CAT_SECURITY),
    ("sentinel", CAT_SECURITY),
    ("camera", CAT_SECURITY),
    ("doorbell", CAT_SECURITY),
    ("security", CAT_SECURITY),
)


def category_for(action_type: str | None) -> str:
    """Map an internal action_type / briefing reason to a driving category."""
    key = (action_type or "").strip().lower()
    if not key:
        return CAT_GENERAL
    if key in _EXACT:
        return _EXACT[key]
    for prefix, cat in _PREFIX:
        if key.startswith(prefix):
            return cat
    return CAT_GENERAL


# ── Config helpers ───────────────────────────────────────────────────────────
def _cfg(config, key: str, default=None):
    """Read a key from the resolved config (dict or attr object). Treats None /
    "" as absent so a blank field falls back to the default."""
    try:
        if config is None:
            return default
        if hasattr(config, "get"):
            val = config.get(key, default)
        else:
            val = getattr(config, key, default)
        return default if val in (None, "") else val
    except Exception:
        return default


def _as_list(raw) -> list[str]:
    """Coerce a config value into a list of entity_ids. Accepts a real list, a
    JSON-encoded list (how the panel persists list keys), or a comma-separated
    / single string — mirroring how other list keys are read across the code."""
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(x) for x in raw if x]
    s = str(raw).strip()
    if not s:
        return []
    if s.startswith("["):
        try:
            j = json.loads(s)
            if isinstance(j, list):
                return [str(x) for x in j if x]
        except Exception:
            pass
    return [p.strip() for p in s.split(",") if p.strip()]


def enabled(config) -> bool:
    """Master switch for the whole feature."""
    return bool(_cfg(config, CONF_DRIVING_MODE_ENABLED, DEFAULT_DRIVING_MODE_ENABLED))


def category_enabled(config, category: str) -> bool:
    """Whether this category is opted in to car-screen routing. The catch-all
    (CAT_GENERAL) is never routed."""
    if category == CAT_BRIEFING:
        return bool(_cfg(config, CONF_DRIVING_NOTIFY_BRIEFINGS, DEFAULT_DRIVING_NOTIFY_BRIEFINGS))
    if category == CAT_TRAVEL:
        return bool(_cfg(config, CONF_DRIVING_NOTIFY_TRAVEL, DEFAULT_DRIVING_NOTIFY_TRAVEL))
    if category == CAT_SECURITY:
        return bool(_cfg(config, CONF_DRIVING_NOTIFY_SECURITY, DEFAULT_DRIVING_NOTIFY_SECURITY))
    return False


def suppress_home_audio(config) -> bool:
    """When driving, skip the (pointless) spoken announcement to home speakers
    for a routed alert. On by default."""
    return bool(_cfg(config, CONF_DRIVING_SUPPRESS_HOME, DEFAULT_DRIVING_SUPPRESS_HOME))


# ── Car-connection detection ─────────────────────────────────────────────────
_ON_STATES = {"on", "home", "connected", "true", "1"}


def discover_car_sensors(hass) -> list[str]:
    """Best-effort auto-discovery of the HA companion app's Android-Auto
    "connected to head unit" binary sensor(s). Matches any household's phones
    without configuration — the companion app names it ``*_android_auto``."""
    out: list[str] = []
    try:
        for st in hass.states.async_all("binary_sensor"):
            eid = getattr(st, "entity_id", "") or ""
            low = eid.lower()
            if low.endswith("_android_auto") or "android_auto" in low:
                out.append(eid)
    except Exception:
        pass
    return out


def car_sensors(hass, config) -> list[str]:
    """Configured head-unit sensor(s), or auto-discovered ones when unset."""
    ids = _as_list(_cfg(config, CONF_DRIVING_CAR_SENSOR, ""))
    if ids:
        return ids
    return discover_car_sensors(hass)


def is_car_connected(hass, config) -> bool:
    """True if any resolved head-unit sensor currently reads connected/on."""
    for eid in car_sensors(hass, config):
        try:
            st = hass.states.get(eid)
        except Exception:
            st = None
        if st is not None and str(getattr(st, "state", "")).lower() in _ON_STATES:
            return True
    return False


# ── Routing decision + payload augmentation ──────────────────────────────────
def should_route(hass, config, category: str) -> bool:
    """The single gate: feature on, this category opted in, car connected now."""
    if not enabled(config):
        return False
    if not category_enabled(config, category):
        return False
    return is_car_connected(hass, config)


def car_ui_fields() -> dict:
    """The mobile_app ``data`` keys that surface a notification on the Android
    Auto screen. A dedicated high-importance channel is what lets it pop over
    the car UI (the companion app requires a poppable channel for AA)."""
    return {
        "car_ui": True,
        "channel": DRIVING_CHANNEL,
        "importance": "high",
        "ttl": 0,
        "priority": "high",
    }


def augment_data_for_category(hass, config, category: str, base_data: dict | None = None) -> dict:
    """Merge the car_ui fields into ``base_data`` when this category should be
    routed to the car; otherwise return ``base_data`` unchanged. Any existing
    image/attachment keys are preserved."""
    data = dict(base_data or {})
    if should_route(hass, config, category):
        data.update(car_ui_fields())
    return data


def augment_data(hass, config, action_type: str | None, base_data: dict | None = None) -> dict:
    """Category-resolving convenience wrapper over :func:`augment_data_for_category`
    for callers that only have the internal action_type / reason string."""
    return augment_data_for_category(hass, config, category_for(action_type), base_data)
