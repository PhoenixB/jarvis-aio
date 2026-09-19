"""Tests for the secrets writer + credential relocation (v6.83.0).

The writer must preserve the rest of the user's secrets.yaml and never lose
data; relocation must write→verify→strip and, on any failure, leave config.json
untouched so a credential can never be lost or auth broken.
"""
import pytest

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


@pytest.fixture
def hs(load):
    return load("ha_secrets")


@pytest.fixture(autouse=True)
def _isolate_jarvis_config(load, tmp_path, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(jc, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(jc, "_cache", {})
    monkeypatch.setattr(jc, "_loaded", True)
    monkeypatch.setattr(jc, "_entry_credential_fallback", {})


async def test_relocate_entry_credentials_writes_missing_secret(hs, fake_hass, tmp_path, monkeypatch):
    p = tmp_path / "secrets.yaml"
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    entry = type("Entry", (), {
        "data": {"api_key": "legacy-key"},
        "options": {},
    })()
    assert await hs.relocate_entry_credentials(fake_hass, entry) == 1
    assert hs.get_secret_sync("jarvis_api_key", path=p) == "legacy-key"
    # The plaintext copy must not linger in the config entry once it is safely
    # in secrets.yaml.
    assert "api_key" not in entry.data


async def test_relocate_entry_credentials_maps_legacy_key_to_selected_provider(
    hs, fake_hass, tmp_path, monkeypatch,
):
    """Pre-multi-provider installs always stored the credential under the
    shared `api_key` field regardless of which provider was configured — the
    migrated secret must land under that provider's real field, not the
    generic (and never read back) `jarvis_api_key`."""
    p = tmp_path / "secrets.yaml"
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    entry = type("Entry", (), {
        "data": {"api_key": "legacy-openai-key", "llm_provider": "openai"},
        "options": {},
    })()
    assert await hs.relocate_entry_credentials(fake_hass, entry) == 1
    assert hs.get_secret_sync("jarvis_openai_api_key", path=p) == "legacy-openai-key"
    assert hs.get_secret_sync("jarvis_api_key", path=p) is None
    assert await hs.async_get_provider_key(fake_hass, "openai") == "legacy-openai-key"


async def test_relocate_entry_credentials_uses_effective_provider_from_panel_config(
    hs, fake_hass, tmp_path, monkeypatch, load,
):
    jc = load("jarvis_config")
    p = tmp_path / "secrets.yaml"
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    monkeypatch.setattr(
        jc,
        "effective_config",
        lambda entry=None: {"llm_provider": "openai", "api_key": "legacy-openai-key"},
    )
    entry = type("Entry", (), {
        "data": {"api_key": "legacy-openai-key"},
        "options": {},
    })()

    assert await hs.relocate_entry_credentials(fake_hass, entry) == 1
    assert hs.get_secret_sync("jarvis_openai_api_key", path=p) == "legacy-openai-key"
    assert hs.get_secret_sync("jarvis_api_key", path=p) is None


async def test_relocate_entry_credentials_prefers_provider_specific_over_shared_api_key(
    hs, fake_hass, tmp_path, monkeypatch,
):
    p = tmp_path / "secrets.yaml"
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    entry = type("Entry", (), {
        "data": {
            "llm_provider": "openai",
            "api_key": "stale-shared-key",
            "openai_api_key": "fresh-openai-key",
        },
        "options": {},
    })()
    assert await hs.relocate_entry_credentials(fake_hass, entry) == 1
    assert hs.get_secret_sync("jarvis_openai_api_key", path=p) == "fresh-openai-key"
    assert "api_key" not in entry.data
    assert "openai_api_key" not in entry.data


async def test_relocate_entry_credentials_prefers_groq_alias_over_shared_api_key(
    hs, fake_hass, tmp_path, monkeypatch,
):
    p = tmp_path / "secrets.yaml"
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    entry = type("Entry", (), {
        "data": {
            "llm_provider": "groq",
            "api_key": "stale-shared-key",
            "groq_api_key": "fresh-groq-key",
        },
        "options": {},
    })()

    assert await hs.relocate_entry_credentials(fake_hass, entry) == 1
    assert hs.get_secret_sync("jarvis_api_key", path=p) == "fresh-groq-key"
    assert "api_key" not in entry.data
    assert "groq_api_key" not in entry.data


async def test_relocate_entry_credentials_maps_legacy_groq_alias_to_canonical_secret(
    hs, fake_hass, tmp_path, monkeypatch,
):
    p = tmp_path / "secrets.yaml"
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    entry = type("Entry", (), {
        "data": {"groq_api_key": "legacy-groq-key", "llm_provider": "groq"},
        "options": {},
    })()
    assert await hs.relocate_entry_credentials(fake_hass, entry) == 1
    assert hs.get_secret_sync("jarvis_api_key", path=p) == "legacy-groq-key"
    assert hs.get_secret_sync("jarvis_groq_api_key", path=p) is None
    assert "groq_api_key" not in entry.data


async def test_relocate_plaintext_credentials_uses_entry_provider_for_legacy_key(
    hs, fake_hass, tmp_path, load, monkeypatch,
):
    jc = load("jarvis_config")
    p = tmp_path / "secrets.yaml"
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    monkeypatch.setattr(jc, "get_all", lambda: {"api_key": "legacy-openai-key"})
    deleted = []
    monkeypatch.setattr(jc, "delete", lambda k: deleted.append(k))
    entry = type("Entry", (), {"data": {"llm_provider": "openai"}, "options": {}})()

    assert await hs.relocate_plaintext_credentials(fake_hass, entry) == 1
    assert hs.get_secret_sync("jarvis_openai_api_key", path=p) == "legacy-openai-key"
    assert hs.get_secret_sync("jarvis_api_key", path=p) is None
    assert deleted == ["api_key"]


async def test_relocate_plaintext_credentials_maps_legacy_groq_alias_to_canonical_secret(
    hs, fake_hass, tmp_path, load, monkeypatch,
):
    jc = load("jarvis_config")
    p = tmp_path / "secrets.yaml"
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    monkeypatch.setattr(jc, "get_all", lambda: {"groq_api_key": "legacy-groq-key"})
    deleted = []
    monkeypatch.setattr(jc, "delete", lambda k: deleted.append(k))

    assert await hs.relocate_plaintext_credentials(fake_hass) == 1
    assert hs.get_secret_sync("jarvis_api_key", path=p) == "legacy-groq-key"
    assert hs.get_secret_sync("jarvis_groq_api_key", path=p) is None
    assert deleted == ["groq_api_key"]


# ── line upsert ──────────────────────────────────────────────────────────────

def test_upsert_appends_when_absent(hs):
    out = hs._upsert_secret_line("existing: 1\n", "jarvis_api_key", "K")
    assert "existing: 1" in out
    assert 'jarvis_api_key: "K"' in out


def test_upsert_replaces_when_present(hs):
    out = hs._upsert_secret_line('a: 1\njarvis_api_key: "OLD"\nb: 2\n',
                                 "jarvis_api_key", "NEW")
    assert 'jarvis_api_key: "NEW"' in out
    assert '"OLD"' not in out
    assert "a: 1" in out and "b: 2" in out            # rest preserved


@pytest.mark.skipif(yaml is None, reason="PyYAML unavailable")
def test_upsert_escapes_quotes_and_backslashes(hs):
    out = hs._upsert_secret_line("", "k", 'a"b\\c')
    assert yaml.safe_load(out)["k"] == 'a"b\\c'


# ── set_secret_sync (safe write) ─────────────────────────────────────────────

@pytest.mark.skipif(yaml is None, reason="PyYAML unavailable")
def test_set_secret_writes_and_preserves(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text("# my secrets\nother_key: value\n")
    assert hs.set_secret_sync("jarvis_api_key", "K", path=p) is True
    text = p.read_text()
    assert "# my secrets" in text                     # comment preserved
    assert "other_key: value" in text                 # other key preserved
    assert yaml.safe_load(text)["jarvis_api_key"] == "K"
    assert (tmp_path / "secrets.yaml.jarvis.bak").exists()   # backup made


@pytest.mark.skipif(yaml is None, reason="PyYAML unavailable")
def test_set_secret_updates_existing(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text('jarvis_api_key: "OLD"\n')
    hs.set_secret_sync("jarvis_api_key", "NEW", path=p)
    assert yaml.safe_load(p.read_text())["jarvis_api_key"] == "NEW"


@pytest.mark.skipif(yaml is None, reason="PyYAML unavailable")
def test_set_secret_creates_missing_file(hs, tmp_path):
    p = tmp_path / "sub" / "secrets.yaml"
    assert hs.set_secret_sync("jarvis_api_key", "K", path=p) is True
    assert yaml.safe_load(p.read_text())["jarvis_api_key"] == "K"


def test_set_secret_empty_key_false(hs, tmp_path):
    assert hs.set_secret_sync("", "K", path=tmp_path / "s.yaml") is False


# ── overlay_credentials ──────────────────────────────────────────────────────

def test_overlay_secrets_win(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text('jarvis_gemini_api_key: "SECRET_G"\n')
    out = hs.overlay_credentials({"gemini_api_key": "PLAIN_G", "model": "x"}, path=p)
    assert out["gemini_api_key"] == "SECRET_G"        # secrets win for creds
    assert out["model"] == "x"                        # non-cred untouched


def test_overlay_absent_secret_no_change(hs, tmp_path):
    p = tmp_path / "secrets.yaml"
    p.write_text('unrelated: "x"\n')
    out = hs.overlay_credentials({"api_key": "PLAIN"}, path=p)
    assert out["api_key"] == "PLAIN"


# ── relocate_plaintext_credentials (verify-before-strip) ─────────────────────

@pytest.mark.skipif(yaml is None, reason="PyYAML unavailable")
async def test_relocate_writes_verifies_strips(hs, fake_hass, tmp_path, load, monkeypatch):
    jc = load("jarvis_config")
    p = tmp_path / "secrets.yaml"
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    deleted = []
    monkeypatch.setattr(jc, "get_all",
                        lambda: {"gemini_api_key": "GKEY", "api_key": "", "model": "x"})
    monkeypatch.setattr(jc, "delete", lambda k: deleted.append(k))
    n = await hs.relocate_plaintext_credentials(fake_hass)
    assert n == 1
    assert deleted == ["gemini_api_key"]              # only the non-empty cred moved
    assert hs.get_secret_sync("jarvis_gemini_api_key", path=p) == "GKEY"


async def test_relocate_leaves_config_when_write_fails(hs, fake_hass, tmp_path, load, monkeypatch):
    jc = load("jarvis_config")
    monkeypatch.setattr(hs, "SECRETS_PATH", tmp_path / "secrets.yaml")
    deleted = []
    monkeypatch.setattr(jc, "get_all", lambda: {"api_key": "KEY"})
    monkeypatch.setattr(jc, "delete", lambda k: deleted.append(k))
    monkeypatch.setattr(hs, "set_secret_sync", lambda k, v, path=None: False)
    n = await hs.relocate_plaintext_credentials(fake_hass)
    assert n == 0
    assert deleted == []                              # never stripped on write failure


async def test_relocate_drops_redundant_when_same(hs, fake_hass, tmp_path, load, monkeypatch):
    jc = load("jarvis_config")
    p = tmp_path / "secrets.yaml"
    p.write_text('jarvis_api_key: "KEY"\n')
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    deleted = []
    monkeypatch.setattr(jc, "get_all", lambda: {"api_key": "KEY"})
    monkeypatch.setattr(jc, "delete", lambda k: deleted.append(k))
    n = await hs.relocate_plaintext_credentials(fake_hass)
    assert n == 1 and deleted == ["api_key"]          # redundant plaintext dropped


async def test_relocate_overwrites_stale_secret_when_different(hs, fake_hass, tmp_path, load, monkeypatch):
    # config.json is retired for credentials after this migration, so on a
    # conflict its (freshest) value wins over a stale secrets.yaml entry —
    # otherwise an old secret would permanently shadow a newly-entered key.
    jc = load("jarvis_config")
    p = tmp_path / "secrets.yaml"
    p.write_text('jarvis_api_key: "SECRETVAL"\n')
    monkeypatch.setattr(hs, "SECRETS_PATH", p)
    deleted = []
    monkeypatch.setattr(jc, "get_all", lambda: {"api_key": "DIFFERENT"})
    monkeypatch.setattr(jc, "delete", lambda k: deleted.append(k))
    n = await hs.relocate_plaintext_credentials(fake_hass)
    assert n == 1 and deleted == ["api_key"]
    assert hs.get_secret_sync("jarvis_api_key", path=p) == "DIFFERENT"
