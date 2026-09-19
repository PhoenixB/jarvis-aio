import pytest


@pytest.fixture
def observer(load):
    return load("observer")


def _snapshot(observer):
    return {
        "config": dict(observer._STATE.config),
        "classifier": observer._STATE.classifier_provider,
        "reasoning": observer._STATE.reasoning_provider,
        "review": observer._STATE.review_provider,
    }


def _restore(observer, snap):
    observer._STATE.config = snap["config"]
    observer._STATE.classifier_provider = snap["classifier"]
    observer._STATE.reasoning_provider = snap["reasoning"]
    observer._STATE.review_provider = snap["review"]


async def test_refresh_tier_providers_overlays_secrets_before_rebuild(
    observer, fake_hass, load, monkeypatch,
):
    hs = load("ha_secrets")
    snap = _snapshot(observer)
    observer._STATE.config = {
        "classifier_provider": "groq",
        "reasoning_provider": "groq",
        "review_provider": "groq",
        "api_key": "stale",
    }
    seen = []

    monkeypatch.setattr(
        hs,
        "overlay_credentials",
        lambda cfg: {**cfg, "api_key": "fresh"},
    )
    monkeypatch.setattr(
        observer,
        "create_tier_provider",
        lambda config, tier: seen.append((tier, config["api_key"])) or f"{tier}:{config['api_key']}",
    )

    try:
        await observer.refresh_tier_providers(fake_hass)
    finally:
        _restore(observer, snap)

    assert seen == [
        ("classifier", "fresh"),
        ("reasoning", "fresh"),
        ("review", "fresh"),
    ]


async def test_refresh_tier_providers_skips_unconfigured_review(observer, fake_hass, monkeypatch):
    snap = _snapshot(observer)
    observer._STATE.config = {
        "classifier_provider": "groq",
        "reasoning_provider": "groq",
        "api_key": "fresh",
    }
    calls = []

    monkeypatch.setattr(
        observer,
        "create_tier_provider",
        lambda config, tier: calls.append(tier) or tier,
    )

    try:
        await observer.refresh_tier_providers(fake_hass)
        assert observer._STATE.review_provider is None
    finally:
        _restore(observer, snap)

    assert calls == ["classifier", "reasoning"]


def test_review_tier_is_not_enabled_by_migrated_gemini_defaults(observer):
    assert observer._review_tier_is_configured({
        "review_provider": "gemini",
        "review_model": "gemini-2.5-pro",
        "gemini_api_key": "gk",
        "review_enabled": False,
    }) is False


def test_review_tier_accepts_explicit_opt_in(observer):
    assert observer._review_tier_is_configured({
        "review_provider": "gemini",
        "review_model": "gemini-2.5-pro",
        "gemini_api_key": "gk",
        "review_enabled": True,
    }) is True


def test_create_tier_provider_defaults_required_tiers_to_main_provider(
    load, monkeypatch,
):
    llm_provider = load("llm_provider")
    monkeypatch.setattr(
        llm_provider,
        "create_provider",
        lambda provider_name, api_key, model, base_url=None: {
            "provider": provider_name,
            "api_key": api_key,
            "model": model,
            "base_url": base_url,
        },
    )

    out = llm_provider.create_tier_provider({
        "llm_provider": "openai",
        "model": "gpt-4o-mini",
        "openai_api_key": "sk-openai",
    }, "classifier")

    assert out == {
        "provider": "openai",
        "api_key": "sk-openai",
        "model": "gpt-4o-mini",
        "base_url": None,
    }


async def test_refresh_tier_providers_updates_proactive_briefing_config(
    observer, fake_hass, load, monkeypatch,
):
    proactive_briefing = load("proactive_briefing")
    snap = _snapshot(observer)
    old_proactive_config = dict(proactive_briefing._STATE.config)
    observer._STATE.config = {
        "classifier_provider": "groq",
        "reasoning_provider": "groq",
        "api_key": "fresh",
    }

    monkeypatch.setattr(
        observer,
        "create_tier_provider",
        lambda config, tier: tier,
    )

    try:
        await observer.refresh_tier_providers(fake_hass, {"reasoning_model": "updated"})
        assert proactive_briefing._STATE.config["reasoning_model"] == "updated"
    finally:
        proactive_briefing._STATE.config = old_proactive_config
        _restore(observer, snap)
