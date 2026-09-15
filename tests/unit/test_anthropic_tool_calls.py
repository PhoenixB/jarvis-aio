"""Anthropic has no "tool" role: a tool call is an assistant `tool_use` content
block, and its result is a `tool_result` block inside a *user* message. Our
callers (agent.py) build conversation history in OpenAI's shape
(assistant.tool_calls + role="tool") for every provider, so AnthropicProvider
must translate it — regression for the "Unexpected role tool" 400 that broke
every tool-using turn."""
import json

import pytest


@pytest.fixture
def provider(load):
    llm = load("llm_provider")
    p = object.__new__(llm.AnthropicProvider)
    p.model = "claude-sonnet-5"
    p.base_url = None
    return p


class _Resp:
    def __init__(self):
        self.content = []


class _FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _Resp()


class _FakeClient:
    def __init__(self):
        self.messages = _FakeMessages()


def test_assistant_tool_calls_become_tool_use_blocks(provider):
    provider._client = _FakeClient()
    messages = [
        {"role": "system", "content": "You are JARVIS."},
        {"role": "user", "content": "turn on the lights"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "turn_on", "arguments": '{"entity": "light.kitchen"}'}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "done"},
    ]
    provider.chat(messages, max_tokens=100, temperature=0.5)
    sent = provider._client.messages.calls[0]
    assert sent["system"] == "You are JARVIS."
    roles = [m["role"] for m in sent["messages"]]
    assert roles == ["user", "assistant", "user"]   # no bare "tool" role
    assistant_msg = sent["messages"][1]
    assert assistant_msg["content"][0]["type"] == "tool_use"
    assert assistant_msg["content"][0]["id"] == "call_1"
    assert assistant_msg["content"][0]["name"] == "turn_on"
    assert assistant_msg["content"][0]["input"] == {"entity": "light.kitchen"}
    tool_result_msg = sent["messages"][2]
    assert tool_result_msg["content"] == [
        {"type": "tool_result", "tool_use_id": "call_1", "content": "done"},
    ]


def test_consecutive_tool_results_merge_into_one_user_message(provider):
    provider._client = _FakeClient()
    messages = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "a", "arguments": "{}"}},
                {"id": "call_2", "type": "function",
                 "function": {"name": "b", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "res1"},
        {"role": "tool", "tool_call_id": "call_2", "content": "res2"},
    ]
    provider.chat(messages, max_tokens=100, temperature=0.5)
    sent = provider._client.messages.calls[0]
    roles = [m["role"] for m in sent["messages"]]
    assert roles == ["assistant", "user"]      # both tool results in ONE message
    ids = [b["tool_use_id"] for b in sent["messages"][1]["content"]]
    assert ids == ["call_1", "call_2"]


def test_assistant_text_kept_alongside_tool_use(provider):
    provider._client = _FakeClient()
    messages = [
        {
            "role": "assistant",
            "content": "Sure, one sec.",
            "tool_calls": [
                {"id": "call_1", "type": "function",
                 "function": {"name": "a", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "ok"},
    ]
    provider.chat(messages, max_tokens=100, temperature=0.5)
    sent = provider._client.messages.calls[0]
    assistant_msg = sent["messages"][0]
    assert assistant_msg["content"][0] == {"type": "text", "text": "Sure, one sec."}
    assert assistant_msg["content"][1]["type"] == "tool_use"
