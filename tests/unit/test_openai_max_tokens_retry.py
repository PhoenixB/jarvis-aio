"""Newer OpenAI models (o1/o3/gpt-5.x reasoning family) reject the classic
`max_tokens` outright ("Unsupported parameter: 'max_tokens' ... Use
'max_completion_tokens' instead"). chat() must retry once with the renamed
param rather than surfacing a 400 and falling back to a different provider."""
import pytest


@pytest.fixture
def provider(load):
    llm = load("llm_provider")
    # Bypass __init__ (it imports the real `openai` package) and wire up a
    # fake client directly — only chat()'s retry logic is under test.
    p = object.__new__(llm.OpenAIProvider)
    p.model = "gpt-5.6-sol"
    p.base_url = None
    return p


class _Msg:
    def __init__(self, content="ok"):
        self.content = content
        self.tool_calls = None


class _Choice:
    def __init__(self, content="ok", finish_reason="stop"):
        self.message = _Msg(content)
        self.finish_reason = finish_reason


class _Resp:
    def __init__(self, content="ok", finish_reason="stop"):
        self.choices = [_Choice(content, finish_reason)]


class _FakeCompletions:
    def __init__(self, fail_once_on_max_tokens=False):
        self._fail_once = fail_once_on_max_tokens
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._fail_once and "max_tokens" in kwargs:
            raise Exception(
                "Error code: 400 - {'error': {'message': \"Unsupported "
                "parameter: 'max_tokens' is not supported with this model. "
                "Use 'max_completion_tokens' instead.\", 'type': "
                "'invalid_request_error', 'param': 'max_tokens', 'code': "
                "'unsupported_parameter'}}"
            )
        return _Resp()


class _FakeChat:
    def __init__(self, fail_once_on_max_tokens=False):
        self.completions = _FakeCompletions(fail_once_on_max_tokens)


class _FakeClient:
    def __init__(self, fail_once_on_max_tokens=False):
        self.chat = _FakeChat(fail_once_on_max_tokens)


def test_retries_with_renamed_param_on_unsupported_error(provider):
    provider._client = _FakeClient(fail_once_on_max_tokens=True)
    result = provider.chat([{"role": "user", "content": "hi"}], max_tokens=5, temperature=0.7)
    assert result["text"] == "ok"
    calls = provider._client.chat.completions.calls
    assert len(calls) == 2
    assert calls[0]["max_tokens"] == 5
    assert "max_completion_tokens" not in calls[0]
    assert calls[1]["max_completion_tokens"] == 5
    assert "max_tokens" not in calls[1]


def test_does_not_retry_on_unrelated_error(provider):
    class _AlwaysFails(_FakeCompletions):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            raise Exception("401 invalid api key")

    provider._client = _FakeClient()
    provider._client.chat.completions = _AlwaysFails()
    with pytest.raises(Exception, match="invalid api key"):
        provider.chat([{"role": "user", "content": "hi"}], max_tokens=5, temperature=0.7)
    assert len(provider._client.chat.completions.calls) == 1   # no pointless retry


def test_normal_call_unaffected(provider):
    provider._client = _FakeClient(fail_once_on_max_tokens=False)
    result = provider.chat([{"role": "user", "content": "hi"}], max_tokens=5, temperature=0.7)
    assert result["text"] == "ok"
    assert len(provider._client.chat.completions.calls) == 1


# ── reasoning models (o1/o3/gpt-5.x): empty content + finish_reason=length ──
# means the token budget was entirely consumed by hidden reasoning, with no
# error raised — must retry once with a much larger budget instead of quietly
# returning nothing.

class _EmptyThenFullCompletions:
    """Call 1: classic max_tokens rejected outright (reasoning model).
    Call 2 (renamed to max_completion_tokens): budget spent entirely on
    hidden reasoning, empty visible content. Call 3 (bigger budget): real
    text."""
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise Exception(
                "Error code: 400 - {'error': {'message': \"Unsupported "
                "parameter: 'max_tokens' is not supported with this model. "
                "Use 'max_completion_tokens' instead.\"}}"
            )
        if len(self.calls) == 2:
            return _Resp(content="", finish_reason="length")
        return _Resp(content="the answer", finish_reason="stop")


def test_retries_with_bigger_budget_on_empty_reasoning_output(provider):
    fake_completions = _EmptyThenFullCompletions()
    provider._client = _FakeClient()
    provider._client.chat.completions = fake_completions
    result = provider.chat(
        [{"role": "user", "content": "hi"}],
        max_tokens=5, temperature=0.7,
    )
    assert result["text"] == "the answer"
    calls = fake_completions.calls
    assert len(calls) == 3
    assert calls[2]["max_completion_tokens"] > calls[1]["max_completion_tokens"]


def test_no_retry_when_length_truncation_has_real_content(provider):
    # finish_reason=length with actual visible text is a normal truncation,
    # not the "all-budget-to-reasoning" failure mode — must not retry.
    class _TruncatedButHasText(_FakeCompletions):
        def create(self, **kwargs):
            self.calls.append(kwargs)
            return _Resp(content="partial answer", finish_reason="length")

    fake = _TruncatedButHasText()
    provider._client = _FakeClient()
    provider._client.chat.completions = fake
    result = provider.chat([{"role": "user", "content": "hi"}], max_tokens=5, temperature=0.7)
    assert result["text"] == "partial answer"
    assert len(fake.calls) == 1   # real content present — no retry needed
