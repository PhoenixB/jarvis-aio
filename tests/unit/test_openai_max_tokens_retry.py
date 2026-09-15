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
    def __init__(self, content="ok"):
        self.message = _Msg(content)


class _Resp:
    def __init__(self, content="ok"):
        self.choices = [_Choice(content)]


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
