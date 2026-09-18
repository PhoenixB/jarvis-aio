from __future__ import annotations

import importlib
import importlib.util
import pathlib
import sys
import types


def _load_websocket_module():
    comp = pathlib.Path(__file__).resolve().parents[2] / "custom_components" / "jarvis"
    sys.modules.pop("jc.websocket", None)

    ws_api = types.ModuleType("homeassistant.components.websocket_api")
    ws_api.websocket_command = lambda schema: (lambda func: func)
    ws_api.async_response = lambda func: func
    ws_api.async_register_command = lambda hass, func: None
    sys.modules["homeassistant.components.websocket_api"] = ws_api
    import homeassistant.components as ha_components

    ha_components.websocket_api = ws_api

    spec = importlib.util.spec_from_file_location("jc.websocket", comp / "websocket.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["jc.websocket"] = mod
    spec.loader.exec_module(mod)
    return mod


async def test_configured_providers_require_custom_endpoint(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    ha_secrets = importlib.import_module("jc.ha_secrets")
    values = {
        "llm_provider": "custom",
        "custom_base_url": "",
        "ollama_base_url": "",
        "llm_base_url": "",
    }
    monkeypatch.setattr(websocket, "_runtime_opt", lambda hass, entry, key, default=None: values.get(key, default))
    monkeypatch.setattr(websocket, "_runtime_opt", lambda hass, entry, key, default=None: values.get(key, default))

    async def _no_key(hass, provider):
        return ""

    monkeypatch.setattr(ha_secrets, "async_get_provider_key", _no_key)

    configured = await websocket._configured_providers(fake_hass, object())
    assert "custom" not in configured


async def test_configured_providers_allow_selected_ollama_default_endpoint(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    ha_secrets = importlib.import_module("jc.ha_secrets")
    values = {
        "llm_provider": "ollama",
        "ollama_base_url": "",
        "llm_base_url": "",
    }
    monkeypatch.setattr(websocket, "_runtime_opt", lambda hass, entry, key, default=None: values.get(key, default))

    async def _no_key(hass, provider):
        return ""

    monkeypatch.setattr(ha_secrets, "async_get_provider_key", _no_key)

    configured = await websocket._configured_providers(fake_hass, object())
    assert "ollama" in configured


async def test_fetch_models_uses_resolved_api_key_in_auth_header(fake_hass, monkeypatch):
    websocket = _load_websocket_module()
    seen = {}

    class _Response:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def text(self):
            return ""

        async def json(self):
            return {"data": [{"id": "gpt-4o-mini"}]}

    class _Session:
        def get(self, url, headers=None):
            seen["url"] = url
            seen["headers"] = headers or {}
            return _Response()

    class _Timeout:
        async def __aenter__(self):
            return None

        async def __aexit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(
        sys.modules["homeassistant.helpers.aiohttp_client"],
        "async_get_clientsession",
        lambda hass: _Session(),
    )
    sys.modules["async_timeout"] = types.SimpleNamespace(timeout=lambda seconds: _Timeout())

    models = await websocket._fetch_models(fake_hass, "openai", "sk-openai", "")

    assert models == ["gpt-4o-mini"]
    assert seen["url"] == "https://api.openai.com/v1/models"
    assert seen["headers"]["Authorization"] == "Bearer " + "sk-openai"
