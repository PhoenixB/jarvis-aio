"""Tests for the config-flow LLM connection check (llm_provider.test_connection, v6.92.0)."""
import pytest


@pytest.fixture
def lp(load):
    return load("llm_provider")


class _OkClient:
    def chat(self, messages, tools=None, max_tokens=512, **k):
        return {"content": "pong"}


class _RaiseClient:
    def __init__(self, exc):
        self._exc = exc

    def chat(self, *a, **k):
        raise self._exc


# ── error classification ─────────────────────────────────────────────────────

def test_classify_auth(lp):
    assert lp._classify_conn_error(Exception("401 Unauthorized: invalid api key")) == "invalid_auth"
    assert lp._classify_conn_error(Exception("Authentication failed")) == "invalid_auth"


def test_classify_connect(lp):
    assert lp._classify_conn_error(Exception("Connection refused")) == "cannot_connect"
    assert lp._classify_conn_error(Exception("Cannot connect to host: timed out")) == "cannot_connect"
    assert lp._classify_conn_error(OSError("getaddrinfo failed")) == "cannot_connect"


def test_classify_unknown(lp):
    assert lp._classify_conn_error(Exception("something entirely unexpected")) == "unknown"


# ── test_connection ──────────────────────────────────────────────────────────

async def test_connection_success(lp, fake_hass, monkeypatch):
    monkeypatch.setattr(lp, "create_provider", lambda p, k, m, b=None: _OkClient())
    assert await lp.test_connection(fake_hass, "ollama", "", "gemma4:26b",
                                    "http://x:11434/v1") is None


async def test_connection_no_client(lp, fake_hass, monkeypatch):
    monkeypatch.setattr(lp, "create_provider", lambda p, k, m, b=None: None)
    assert await lp.test_connection(fake_hass, "ollama", "", "m", "http://x") == "cannot_connect"


async def test_connection_auth_error(lp, fake_hass, monkeypatch):
    monkeypatch.setattr(lp, "create_provider",
                        lambda p, k, m, b=None: _RaiseClient(Exception("401 invalid api key")))
    assert await lp.test_connection(fake_hass, "groq", "bad", "m", None) == "invalid_auth"


async def test_connection_conn_error(lp, fake_hass, monkeypatch):
    monkeypatch.setattr(lp, "create_provider",
                        lambda p, k, m, b=None: _RaiseClient(OSError("Connection refused")))
    assert await lp.test_connection(fake_hass, "ollama", "", "m",
                                    "http://down:11434") == "cannot_connect"


async def test_connection_build_failure(lp, fake_hass, monkeypatch):
    def _boom(*a, **k):
        raise Exception("could not connect to endpoint")
    monkeypatch.setattr(lp, "create_provider", _boom)
    assert await lp.test_connection(fake_hass, "ollama", "", "m", "http://x") == "cannot_connect"


# ── model-not-found: a valid key probed with a placeholder model must not
# be mistaken for a bad key/endpoint (regression: OpenAI/Anthropic/Gemini all
# failed setup because the config-flow probe uses a Groq-style model id) ────

def test_classify_model_not_found_is_not_auth_or_connect(lp):
    # these must NOT be misrouted to invalid_auth/cannot_connect by the
    # generic classifier — test_connection intercepts them before that
    assert lp._is_model_not_found(Exception("The model `openai/gpt-oss-120b` does not exist"))
    assert lp._is_model_not_found(Exception("404 model_not_found: no such model"))
    assert lp._is_model_not_found(Exception("models/gpt-oss-120b is not found for API version"))
    assert not lp._is_model_not_found(Exception("401 Unauthorized: invalid api key"))
    assert not lp._is_model_not_found(Exception("Connection refused"))


async def test_connection_succeeds_when_probe_model_is_unknown_to_provider(lp, fake_hass, monkeypatch):
    monkeypatch.setattr(lp, "create_provider",
                        lambda p, k, m, b=None: _RaiseClient(
                            Exception("The model `openai/gpt-oss-120b` does not exist")))
    # a real key against the wrong provider's model proves the key/endpoint work
    assert await lp.test_connection(fake_hass, "openai", "sk-real", "openai/gpt-oss-120b", None) is None


async def test_connection_still_fails_on_real_auth_error_with_model_in_message(lp, fake_hass, monkeypatch):
    monkeypatch.setattr(lp, "create_provider",
                        lambda p, k, m, b=None: _RaiseClient(
                            Exception("401 Unauthorized: invalid api key for model access")))
    assert await lp.test_connection(fake_hass, "openai", "bad", "m", None) == "invalid_auth"
