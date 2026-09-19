"""Guards for the conversation entity's HA dispatch contract.

HA's ConversationEntity.async_process is @final and sets up the chat session/log
before calling _async_handle_message. JARVIS must implement ONLY
_async_handle_message and must not override async_process — a shim that did
(v7.48.1) bypassed that setup on current HA and crashed voice turns with an
opaque "Unexpected error during intent recognition". These are source-level
guards because exercising the entity needs a live HA conversation stack.
"""
import inspect
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "custom_components" / "jarvis" / "conversation.py"


def test_does_not_override_final_async_process():
    src = SRC.read_text()
    # No `async def async_process` method on the entity (it's @final in HA).
    assert "async def async_process(" not in src


def test_handler_is_wrapped_against_crashes():
    src = SRC.read_text()
    # The public handler HA calls delegates to an impl inside try/except, so a
    # crash surfaces with a trace instead of HA's opaque error.
    assert "async def _async_handle_message(" in src
    assert "async def _handle_message_impl(" in src
    assert "conversation handler crashed" in src


def test_handler_is_actually_a_method_of_jarvis_agent():
    """The handler MUST be a method of the registered entity class JarvisAgent.
    A stray column-0 statement once closed the class early and orphaned the whole
    handler as dead code, so HA hit its base _async_handle_message and raised
    NotImplementedError. Text-presence isn't enough — check real class membership.
    """
    import ast
    tree = ast.parse(SRC.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "JarvisAgent":
            methods = {
                n.name for n in node.body
                if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef))
            }
            assert "_async_handle_message" in methods, \
                "_async_handle_message is not a method of JarvisAgent (orphaned?)"
            assert "_handle_message_impl" in methods
            return
    raise AssertionError("JarvisAgent class not found")


def test_agentic_loop_uses_standardized_result_fields_not_raw_message():
    """Anthropic's `raw` is a Message with content blocks and no `.tool_calls` —
    building the assistant turn from `raw_message.tool_calls`/`.content` (an
    OpenAI-only shape) raised AttributeError on the first Anthropic tool turn.
    The loop must build the assistant message from the standardized
    result["text"]/result["calls"] fields instead."""
    src = SRC.read_text()
    assert "raw_message" not in src
    assert 'result["calls"]' in src


def test_context_includes_household_temperature_unit():
    """Freeform LLM output must follow Home Assistant's unit system rather than
    defaulting to Fahrenheit. The live-context builder reads the configured
    temperature unit and instructs the model to express temperatures in it, so a
    metric household hears Celsius even when JARVIS isn't quoting a sensor."""
    src = SRC.read_text()
    assert "config.units.temperature_unit" in src, \
        "conversation context must read HA's configured temperature unit"
    assert "temperatures in" in src, \
        "context must instruct the model which temperature unit to use"


import re as _re


def _load_gate_fn():
    """Exec _is_addressed_to_jarvis + its constant deps in isolation (the entity
    needs a live HA stack, so the module can't be imported wholesale)."""
    import ast, types
    src = SRC.read_text()
    tree = ast.parse(src)
    mod = types.ModuleType("conv_gate_stub")
    want_fns = {"_is_addressed_to_jarvis"}
    want_assign = {"_COMMAND_VERBS", "_QUESTION_STARTS", "_DOMAIN_KEYWORDS", "_FILLER"}
    for n in tree.body:
        seg = None
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id in want_assign for t in n.targets):
            seg = ast.get_source_segment(src, n)
        elif isinstance(n, ast.FunctionDef) and n.name in want_fns:
            seg = ast.get_source_segment(src, n)
        if seg is not None:
            exec(compile(seg, "<conv_gate>", "exec"), mod.__dict__)
    return mod.__dict__["_is_addressed_to_jarvis"]


def test_relevance_gate_strict_drops_ambient_during_media():
    g = _load_gate_fn()
    # Real commands pass whether or not media is playing.
    for cmd in ["turn on the lights", "what's the temperature", "jarvis status", "lock the front door"]:
        assert g(cmd, strict=False) is True, cmd
        assert g(cmd, strict=True) is True, cmd
    # TV/movie dialogue fragments: still pass in a quiet room (lenient default,
    # unchanged), but are DROPPED when media is playing nearby.
    for frag in ["um sergeant", "Thank you.",
                 "than you're paid for and they're often knowing that you're"]:
        assert g(frag, strict=False) is True, frag       # lenient default unchanged
        assert g(frag, strict=True) is False, frag        # media playing → dropped


def test_gate_and_followups_are_media_aware():
    src = SRC.read_text()
    assert "_media_playing_near(device_id)" in src, "gate must check media state"
    assert "_is_addressed_to_jarvis(user_input.text, strict=" in src, "gate must pass strict"
    assert "not self._media_playing_near(reopen_device)" in src, \
        "continued-conversation reopen must be suppressed during media"
    m = _re.search(r"def _media_playing_near\(.*?\n(.*?)\n\n    def ", src, _re.S)
    assert m and "movie_media_player" in m.group(1) and "entity_area" in m.group(1), \
        "media detector must check the movie player + the satellite's area"
