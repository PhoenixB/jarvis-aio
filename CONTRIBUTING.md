# Contributing to JARVIS

Thanks for your interest in improving JARVIS. This document covers the repository
layout, the development workflow, and the release process.

## Repository layout

```
jarvis-aio/                         repo root (HACS integration repository)
├── hacs.json                        HACS metadata
├── README.md  CHANGELOG.md  LICENSE
├── icon.png  logo.png               branding (for home-assistant/brands)
├── .github/                         funding, issue templates, CI
├── scripts/bump_version.sh          one-command version bump
│   └── legacy_addon_bootstrap.py    reference for the in-progress in-integration bootstrap
└── custom_components/jarvis/        the integration (domain: jarvis)
    ├── manifest.json
    ├── __init__.py + 47 modules
    └── frontend/jarvis-panel.js     the dashboard
```

HACS installs `custom_components/jarvis/` into Home Assistant. The integration
runs in-process; the config flow (or a migrated legacy config) sets it up.

## Code standard

This project holds to senior+ engineering output:

- **4-pass audit before every release:** (1) syntax, (2) integration references,
  (3) all modules parse, (4) version bump + package.
- **Simulate tests internally** before shipping — the codebase carries standalone
  test harnesses for the reasoning loop, the Local Mind decision matrix, the
  package state machine, cognition salience tiering, and the speech composer.
- **Honest caveats** documented with every change. Verification before speculation.
- No careless mistakes, and no apologies in place of fixes.

## Local checks

These are the same checks the `Validate` CI workflow runs on every push:

```bash
# Full audit: bytecode compile + relative-import resolution + exported-name checks
python3 scripts/audit.py

# Unit tests
python3 -m pytest tests/ -q

# Dashboard JavaScript parses
node --check custom_components/jarvis/frontend/jarvis-panel.js
```

## Releasing

Bump the version everywhere it appears with one command:

```bash
./scripts/bump_version.sh 6.3.3
```

This updates the integration `manifest.json` (the source of truth) and the version
string in `jarvis-panel.js`. Then commit, tag (`git tag 6.3.3` — tags are `X.Y.Z`,
no `v` prefix), and push the tag — the validation workflow runs on every push, and
HACS publishes from the tag (no add-on/Docker build).

After updating on a live system, hard-refresh the browser (`Ctrl+Shift+R`) so the
cached dashboard JavaScript reloads.

## Pull requests

Keep changes focused, include a short rationale, and note any limitations. If a
change touches the reasoning pipeline, the camera/Nest paths, or cognition
salience, please describe how you verified it.
