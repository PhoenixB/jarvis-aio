"""Tests for scripts/extract_changelog.py — the release-notes extractor the
Release workflow feeds to `gh release create`.

Pins the section-boundary logic, title bolding, body trimming, and the
absent-version signal (None → workflow fails loudly instead of an empty
release).
"""
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load():
    spec = importlib.util.spec_from_file_location(
        "extract_changelog", ROOT / "scripts" / "extract_changelog.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["extract_changelog"] = mod
    spec.loader.exec_module(mod)
    return mod


ec = _load()

SAMPLE = """## [7.96.0] — new things

**Feature A.** Did a thing.

**Feature B.** Did another.

## [7.95.0] — older things

Some older body.

## [7.94.0] — oldest
"""


def test_extracts_titled_section_with_body():
    out = ec.extract(SAMPLE, "7.96.0")
    assert out.startswith("**new things**\n\n")
    assert "Feature A" in out and "Feature B" in out
    # stops at the next header — doesn't bleed into 7.95.0
    assert "older body" not in out
    assert "7.95.0" not in out


def test_middle_section_bounded_both_sides():
    out = ec.extract(SAMPLE, "7.95.0")
    assert out == "**older things**\n\nSome older body."


def test_last_section_with_no_following_header_or_body():
    # 7.94.0 is the final header and has no body — title only
    out = ec.extract(SAMPLE, "7.94.0")
    assert out == "**oldest**"


def test_absent_version_returns_none():
    assert ec.extract(SAMPLE, "1.2.3") is None


def test_version_is_matched_literally_not_as_regex():
    # dots in the version must not act as regex wildcards
    assert ec.extract(SAMPLE, "7X96X0") is None


def test_section_without_title_returns_body_only():
    text = "## [1.0.0]\n\nJust a body, no title.\n"
    assert ec.extract(text, "1.0.0") == "Just a body, no title."


def test_real_changelog_has_notes_for_current_manifest_version():
    import json
    version = json.load(
        open(ROOT / "custom_components" / "jarvis" / "manifest.json"))["version"]
    notes = ec.extract((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    assert notes, f"CHANGELOG.md is missing a section for v{version}"


def test_main_missing_version_exits_nonzero(capsys):
    rc = ec.main(["extract_changelog.py", "0.0.0",
                  str(ROOT / "CHANGELOG.md")])
    assert rc == 1
