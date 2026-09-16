"""Guards that safety- and user-action-critical failures are LOGGED (visible in
the HA UI) rather than swallowed silently. Source-level because exercising these
paths needs a live HA stack; the contract we protect is 'this failure is not
invisible', which the source expresses directly.
"""
from pathlib import Path

CC = Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"


def _src(name: str) -> str:
    return (CC / name).read_text()


def test_tv_display_filter_failures_are_warned():
    # A failure in the display-target (TV) filter is the TV-takeover class — it
    # must surface at a visible level, never a silent pass.
    for f in ("tts_helper.py", "observer.py", "proactive_audio.py"):
        s = _src(f)
        assert "display-target filter failed" in s, f
        assert "_LOGGER.warning" in s, f


def test_device_control_failures_are_logged():
    le = _src("local_engine.py")
    assert "goodnight scene/script" in le, "scene failure must log"
    assert "could not lock" in le, "lock failure must log (security)"
    assert "bulk device control failed" in _src("agent.py"), "bulk control must log"


def test_notification_failures_are_logged():
    assert "intrusion notification failed" in _src("cognitive_core.py")
    assert "appliance notification" in _src("appliance_monitor.py")
    assert "config-corruption notice" in _src("__init__.py")
