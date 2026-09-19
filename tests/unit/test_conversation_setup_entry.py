from __future__ import annotations

import types

import pytest


@pytest.mark.asyncio
async def test_setup_entry_fallback_builds_client_async_safe(load, fake_hass, monkeypatch):
    conv = load("conversation")
    hs = load("ha_secrets")
    jc = load("jarvis_config")

    entry = types.SimpleNamespace(entry_id="entry-1", data={}, options={})
    effective = {
        "llm_provider": "custom",
        "model": "test-model",
        "custom_base_url": "http://custom.local",
    }
    effective_config_call = {"count": 0}

    def _effective_config(_entry):
        effective_config_call["count"] += 1
        return effective

    monkeypatch.setattr(jc, "effective_config", _effective_config)
    monkeypatch.setattr(
        hs,
        "get_provider_key_sync",
        lambda provider: (_ for _ in ()).throw(
            AssertionError("sync provider key lookup must not run in constructor")
        ),
    )

    async def _async_get_provider_key(hass, provider):
        assert hass is fake_hass
        assert provider == "custom"
        return "secret-key"

    monkeypatch.setattr(hs, "async_get_provider_key", _async_get_provider_key)

    provider_calls: list[tuple[str, str, str, str | None]] = []
    client = object()

    def _create_provider(provider, api_key, model, base_url):
        provider_calls.append((provider, api_key, model, base_url))
        return client

    monkeypatch.setattr(conv, "create_provider", _create_provider)

    executor_calls = {"count": 0}

    async def _executor_job(func, *args):
        executor_calls["count"] += 1
        return func(*args)

    monkeypatch.setattr(fake_hass, "async_add_executor_job", _executor_job)

    added = []
    await conv.async_setup_entry(fake_hass, entry, lambda entities: added.extend(entities))

    assert effective_config_call["count"] == 1
    assert executor_calls["count"] == 1
    assert provider_calls == [("custom", "secret-key", "test-model", "http://custom.local")]
    assert len(added) == 1
    assert isinstance(added[0], conv.JarvisAgent)
    assert added[0]._client is client


@pytest.mark.asyncio
async def test_setup_entry_uses_shared_client_without_fallback(load, fake_hass, monkeypatch):
    conv = load("conversation")
    hs = load("ha_secrets")
    jc = load("jarvis_config")

    entry = types.SimpleNamespace(entry_id="entry-2", data={}, options={})
    shared_client = object()
    fake_hass.data = {conv.DOMAIN: {entry.entry_id: {"client": shared_client}}}

    monkeypatch.setattr(
        conv,
        "create_provider",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("create_provider should not run when shared client exists")
        ),
    )
    monkeypatch.setattr(
        jc,
        "effective_config",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("effective_config should not run when shared client exists")
        ),
    )
    monkeypatch.setattr(
        hs,
        "async_get_provider_key",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("async key lookup should not run when shared client exists")
        ),
    )
    monkeypatch.setattr(
        hs,
        "get_provider_key_sync",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("sync key lookup should never run in constructor")
        ),
    )
    monkeypatch.setattr(
        fake_hass,
        "async_add_executor_job",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("executor should not run when shared client exists")
        ),
    )

    added = []
    await conv.async_setup_entry(fake_hass, entry, lambda entities: added.extend(entities))

    assert len(added) == 1
    assert isinstance(added[0], conv.JarvisAgent)
    assert added[0]._client is shared_client
