"""JARVIS TTS voice follows the configured quality (issue #26 / v7.94.0).

Requesting en_GB-jarvis-high when only en_GB-jarvis-medium is installed is what
produced VoiceNotFoundError. The voice option must track voice_quality (default
medium, matching the bootstrap installer) and yield to tts_use_ha_voice.
"""
import sys
import types

import pytest


@pytest.fixture
def tts(load, monkeypatch):
    monkeypatch.setitem(sys.modules, "aiohttp", types.ModuleType("aiohttp"))
    sys.modules.pop("jc.tts_helper", None)
    mod = load("tts_helper")
    yield mod
    sys.modules.pop("jc.tts_helper", None)


def test_non_piper_returns_none(tts):
    assert tts._pick_jarvis_voice([{"voice_quality": "high"}], is_piper=False) is None


def test_default_quality_is_medium(tts):
    assert tts._pick_jarvis_voice([], is_piper=True) == "en_GB-jarvis-medium"
    assert tts._pick_jarvis_voice([{}], is_piper=True) == "en_GB-jarvis-medium"


def test_follows_configured_quality(tts):
    assert tts._pick_jarvis_voice([{"voice_quality": "high"}], is_piper=True) == "en_GB-jarvis-high"
    assert tts._pick_jarvis_voice([{"voice_quality": "medium"}], is_piper=True) == "en_GB-jarvis-medium"


def test_ha_voice_overrides(tts):
    assert tts._pick_jarvis_voice([{"tts_use_ha_voice": True}], is_piper=True) is None
    # even with a quality set, HA-voice preference wins
    assert tts._pick_jarvis_voice(
        [{"voice_quality": "high", "tts_use_ha_voice": True}], is_piper=True) is None


def test_tolerates_bad_entries(tts):
    assert tts._pick_jarvis_voice([None, "x", {"voice_quality": "high"}], is_piper=True) == "en_GB-jarvis-high"
