import pytest


@pytest.fixture
def llm(load):
    return load("llm_provider")


async def test_async_refresh_main_client_updates_shared_client_ref(
    llm, fake_hass, load, monkeypatch,
):
    const = load("const")
    hs = load("ha_secrets")
    jc = load("jarvis_config")
    entry = type("Entry", (), {"entry_id": "entry-1"})()
    sentinel = type("Sentinel", (), {"_groq": "old"})()
    runtime = {
        "client": "old",
        "client_ref": {"client": "old"},
        "sentinel": sentinel,
    }
    fake_hass.data[const.DOMAIN] = {entry.entry_id: runtime}

    monkeypatch.setattr(jc, "effective_config", lambda entry: {
        "llm_provider": "openai",
        "model": "gpt-4o-mini",
    })

    async def _provider_key(hass, provider):
        assert provider == "openai"
        return "sk-openai"

    monkeypatch.setattr(hs, "async_get_provider_key", _provider_key)
    monkeypatch.setattr(const, "resolve_provider_base_url", lambda config, provider: "https://api.openai.com/v1")
    monkeypatch.setattr(
        llm,
        "create_provider",
        lambda provider, api_key, model, base_url: {
            "provider": provider,
            "api_key": api_key,
            "model": model,
            "base_url": base_url,
        },
    )

    await llm.async_refresh_main_client(fake_hass, entry)

    assert runtime["client"]["api_key"] == "sk-openai"
    assert runtime["client_ref"]["client"] is runtime["client"]
    assert sentinel._groq is runtime["client"]
