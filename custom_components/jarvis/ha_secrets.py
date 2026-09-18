"""
JARVIS — Home Assistant secrets.yaml resolver (v6.81.0).

Credentials and passwords belong in Home Assistant's secrets.yaml, not in the
plaintext panel config (/config/jarvis/config.json). This module is the single
controlled bridge to that file: it resolves named secrets and performs narrow,
atomic provider-key writes during setup or migration. It never exposes
credentials to panel/runtime config storage and does not modify unrelated
user-managed secrets.

Reads tolerate a missing or malformed file (returning the default and logging,
never raising), so a secrets typo cannot take integration setup down — the same
"sideline, don't crash" discipline jarvis_config learned the hard way.

Blocking file I/O is offloaded to HA's executor via async_get_secret; a bare
synchronous reader is exposed for the executor job and for tests.

(v6.81.0 lands the resolver + its first consumer, the mail agent. Migrating the
existing LLM/observer keys onto it is a separate, isolated change.)
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

_LOGGER = logging.getLogger(__name__)

SECRETS_PATH = Path("/config/secrets.yaml")


_SECRETS_CACHE = None            # cached read of the default SECRETS_PATH


def _reset_secrets_cache() -> None:
    global _SECRETS_CACHE
    _SECRETS_CACHE = None


def _read_secrets(path: Path | None = None, force: bool = False) -> dict:
    """Parse secrets.yaml into a dict.

    Missing file → {} (not an error — many installs have none). Malformed YAML,
    or a top level that isn't a mapping → {} plus a warning. Never raises.
    Blocking — call via the executor from async code.

    `path` is resolved at call time (default SECRETS_PATH), not bound at
    definition — otherwise a test monkeypatching SECRETS_PATH wouldn't take,
    the same default-binding trap the DB layer hit.
    """
    global _SECRETS_CACHE
    use_default = path is None
    if path is None:
        path = SECRETS_PATH
    if (use_default and not force and _SECRETS_CACHE is not None
            and _SECRETS_CACHE[0] == path):
        return _SECRETS_CACHE[1]
    result: dict = {}
    try:
        if path.exists():
            import yaml  # PyYAML ships with Home Assistant core
            with open(path) as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                result = data
            elif data is not None:
                _LOGGER.warning(
                    "JARVIS: %s top level is %s, expected a mapping — ignoring",
                    path, type(data).__name__,
                )
    except Exception as exc:  # yaml.YAMLError, OSError, UnicodeDecodeError, …
        _LOGGER.warning("JARVIS: could not read %s: %s", path, exc)
        result = {}
    if use_default:
        _SECRETS_CACHE = (path, result)
    return result


def get_secret_sync(key: str, default: Any = None,
                    path: Path | None = None) -> Any:
    """Synchronous secret lookup (blocking).

    Prefer async_get_secret from async code so the read runs off the event loop.
    An empty string in secrets.yaml is treated as unset (returns `default`).
    `path` resolves at call time (default SECRETS_PATH).
    """
    if not key:
        return default
    val = _read_secrets(path).get(key, default)
    return val if val not in (None, "") else default


async def async_get_secret(hass, key: str, default: Any = None) -> Any:
    """Resolve a named secret from secrets.yaml, off the event loop.

    Returns `default` when the key is absent/empty or the file is unusable.
    Never raises.
    """
    if hass is None:
        return get_secret_sync(key, default)
    try:
        return await hass.async_add_executor_job(get_secret_sync, key, default)
    except Exception as exc:
        _LOGGER.debug("JARVIS async_get_secret(%s) failed: %s", key, exc)
        return default


# ── Credential relocation (v6.83.0) ──────────────────────────────────────────
# Config keys that hold LLM credentials. In secrets.yaml they live namespaced
# under jarvis_<key> so they can't collide with another integration's secrets in
# the shared file.
CREDENTIAL_KEYS = ("api_key", "gemini_api_key", "anthropic_api_key",
                   "openai_api_key", "groq_api_key", "custom_api_key")


def secret_key_for(config_key: str) -> str:
    """secrets.yaml key name for a plaintext config credential key."""
    return "jarvis_" + str(config_key)


def overlay_credentials(config: dict, path: Path | None = None) -> dict:
    """Overlay any credential present in secrets.yaml (under jarvis_<key>) onto
    `config` — secrets.yaml wins for credentials. One file read. Mutates and
    returns `config`. Never raises."""
    try:
        secrets = _read_secrets(path)
    except Exception:
        return config
    if not secrets:
        return config
    for ck in CREDENTIAL_KEYS:
        sv = secrets.get(secret_key_for(ck))
        if sv not in (None, ""):
            config[ck] = sv
    return config


def provider_key_name(provider: str) -> Optional[str]:
    """The credential field name for `provider` (const.PROVIDER_API_KEY_FIELDS),
    e.g. 'openai' -> 'openai_api_key'. None for a provider that needs no key
    (ollama) or isn't recognised."""
    from .const import PROVIDER_API_KEY_FIELDS
    return PROVIDER_API_KEY_FIELDS.get(provider)


def get_legacy_provider_key(config: dict[str, Any], provider: str) -> str:
    """A provider's still-plaintext runtime credential, if one exists.

    This is a bounded migration fallback only: secrets.yaml remains the source
    of truth, but older installs may still have the selected provider's key in
    config.json until relocation succeeds.
    """
    field = provider_key_name(provider)
    if not field:
        return ""
    val = config.get(field)
    if val:
        return str(val)
    if provider == "groq":
        val = config.get("groq_api_key")
        if val:
            return str(val)
    val = config.get("api_key") if config.get("llm_provider", "groq") == provider else ""
    return str(val) if val else ""


def get_stored_provider_key_sync(provider: str, path: Path | None = None) -> str:
    """The provider key stored in secrets.yaml only, with no config fallback."""
    field = provider_key_name(provider)
    if not field:
        return ""
    val = get_secret_sync(secret_key_for(field), "", path)
    if not val and provider == "groq":
        # Legacy secret name from before provider-specific fields existed.
        val = get_secret_sync(secret_key_for("groq_api_key"), "", path)
    return val or ""


def get_provider_key_sync(provider: str, path: Path | None = None) -> str:
    """The API key for `provider`, read straight from secrets.yaml — the only
    place credentials live now once migration finishes. While an older install
    still has its selected provider key in config.json and secrets.yaml cannot
    yet be updated, fall back to that legacy in-memory value so auth keeps working.
    Blocking — call via the executor from async code."""
    val = get_stored_provider_key_sync(provider, path)
    if not val and path is None:
        try:
            from . import jarvis_config
            val = get_legacy_provider_key(jarvis_config.get_all(), provider)
            if not val:
                val = jarvis_config.get_entry_credential_fallback(provider)
        except Exception:
            val = ""
    return val or ""


async def async_get_provider_key(hass, provider: str) -> str:
    """:func:`get_provider_key_sync`, off the event loop."""
    if hass is None:
        return get_provider_key_sync(provider)
    return await hass.async_add_executor_job(get_provider_key_sync, provider)


def set_provider_key_sync(provider: str, value: str, path: Path | None = None) -> bool:
    """Store `value` as the API key for `provider` in secrets.yaml. Returns
    False (no-op) for a provider that takes no key (ollama). Blocking."""
    field = provider_key_name(provider)
    if not field:
        return False
    return set_secret_sync(secret_key_for(field), value, path)


async def async_set_provider_key(hass, provider: str, value: str) -> bool:
    """:func:`set_provider_key_sync`, off the event loop."""
    return await hass.async_add_executor_job(set_provider_key_sync, provider, value)


async def relocate_entry_credentials(hass, entry) -> int:
    """Move legacy credential values from a config entry into secrets.yaml.

    Older installs stored credentials in ``entry.data`` or ``entry.options``;
    this runs before provider client construction so those installs do not
    briefly boot with an empty credential after the secrets-only migration.
    Existing secrets win when both locations contain different values. Once a
    key is safely in secrets.yaml it is also removed from the entry so the
    plaintext copy doesn't linger in Home Assistant's config-entry storage.
    """
    data = dict(getattr(entry, "data", {}) or {})
    options = dict(getattr(entry, "options", {}) or {})
    values = dict(data)
    for key, value in options.items():
        # HA may retain an empty options placeholder after an older entry was
        # migrated. Do not let that placeholder hide the real data value.
        if key not in CREDENTIAL_KEYS or value not in (None, ""):
            values[key] = value
    provider = values.get("llm_provider", "groq")
    moved = 0
    migrated_keys = []
    for key in CREDENTIAL_KEYS:
        value = values.get(key)
        if not value:
            continue
        # Pre-multi-provider installs always used the shared `api_key` field
        # regardless of which provider was selected — resolve it to that
        # provider's real field so the migrated secret is actually readable.
        field = key
        if key == "api_key" and provider != "groq":
            field = provider_key_name(provider) or key
        secret_name = secret_key_for(field)
        existing = await hass.async_add_executor_job(get_secret_sync, secret_name, "")
        if not existing:
            if await hass.async_add_executor_job(set_secret_sync, secret_name, value):
                moved += 1
                migrated_keys.append(key)
        else:
            migrated_keys.append(key)  # already in secrets.yaml — still drop the copy

    if migrated_keys:
        new_data = {k: v for k, v in data.items() if k not in migrated_keys}
        new_options = {k: v for k, v in options.items() if k not in migrated_keys}
        if new_data != data or new_options != options:
            hass.config_entries.async_update_entry(entry, data=new_data, options=new_options)
    return moved


def _upsert_secret_line(text: str, key: str, value) -> str:
    """secrets.yaml text with `key: "value"` upserted: replace an existing
    top-level `key:` line if present, else append. The rest of the file is kept
    verbatim (comments, other keys, formatting)."""
    import re
    esc = str(value).replace("\\", "\\\\").replace('"', '\\"')
    line = '%s: "%s"' % (key, esc)
    pat = re.compile(r"(?m)^" + re.escape(key) + r":.*$")
    if pat.search(text):
        return pat.sub(line, text, count=1)
    sep = "" if (text == "" or text.endswith("\n")) else "\n"
    return text + sep + line + "\n"


def set_secret_sync(key: str, value, path: Path | None = None) -> bool:
    """Upsert one secret into secrets.yaml. Safe: backs up the existing file,
    writes atomically via a temp file + rename, preserves the rest of the file.
    Returns True on success. Never raises. Blocking — executor from async."""
    if not key:
        return False
    import os
    import shutil
    import tempfile
    try:
        if path is None:
            path = SECRETS_PATH
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        text = path.read_text() if path.exists() else ""
        new_text = _upsert_secret_line(text, key, value)
        if path.exists():
            shutil.copy2(str(path), str(path) + ".jarvis.bak")
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".secrets-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                f.write(new_text)
            os.replace(tmp, str(path))
            _reset_secrets_cache()   # written file — next read must be fresh
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
        return True
    except Exception as exc:
        _LOGGER.warning("JARVIS: could not write requested secret: %s", exc)
        return False


async def relocate_plaintext_credentials(hass, entry=None) -> int:
    """One-time, safe migration of plaintext LLM credentials out of the panel
    config (config.json) and into secrets.yaml — which is now the ONLY place
    they live; config.json must never hold one again (jarvis_config.set/
    set_many refuse to write these keys going forward).

    Per credential key present in config.json with a real value:
      - already in secrets.yaml with the SAME value -> drop the redundant copy;
      - already there but DIFFERENT -> config.json wins (it reflects the most
        recent config-flow submission; secrets.yaml is retiring config.json's
        write path, so an old/stale secret must not keep shadowing a fresh one);
      - otherwise write it, re-read to VERIFY it's durable, and only then delete
        it from config.json. If write or verify fails, config.json is left
        untouched — the key still resolves via fallback, so auth can't break.

    Returns the count of plaintext copies removed. Never raises.
    """
    from . import jarvis_config
    removed = 0
    try:
        cfg = await hass.async_add_executor_job(jarvis_config.get_all)
    except Exception:
        return 0
    provider = cfg.get("llm_provider", "groq")
    if entry is not None:
        try:
            effective = await hass.async_add_executor_job(jarvis_config.effective_config, entry)
            provider = effective.get("llm_provider", provider)
        except Exception:
            pass
    for ck in CREDENTIAL_KEYS:
        val = cfg.get(ck)
        if not val:
            continue
        # Pre-multi-provider installs always used the shared `api_key` field
        # regardless of which provider was selected — resolve it to that
        # provider's real field so the migrated secret is actually readable.
        field = ck
        if ck == "api_key" and provider != "groq":
            field = provider_key_name(provider) or ck
        skey = secret_key_for(field)
        try:
            existing = await hass.async_add_executor_job(get_secret_sync, skey, None)
            if existing != val:
                ok = await hass.async_add_executor_job(set_secret_sync, skey, val)
                if not ok:
                    continue  # write failed — leave config.json's copy as fallback
                check = await hass.async_add_executor_job(get_secret_sync, skey, None)
                if check != val:
                    continue  # verify failed — leave config.json's copy as fallback
            await hass.async_add_executor_job(jarvis_config.delete, ck)
            removed += 1
        except Exception as exc:
            _LOGGER.debug("JARVIS: relocate %s skipped: %s", ck, exc)
    if removed:
        _LOGGER.info("JARVIS: relocated %d plaintext credential(s) to secrets.yaml", removed)
    return removed
