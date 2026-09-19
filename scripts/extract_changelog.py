#!/usr/bin/env python3
"""Extract one version's section from CHANGELOG.md as release notes.

Usage:
    extract_changelog.py <version> [changelog_path]

Prints the notes for the ``## [<version>] — <title>`` section: the section's
descriptive title (the text after the version) as a bold first line, then the
section body up to — but not including — the next ``## [`` header. Exits 1 if the
version has no section, so the release workflow fails loudly rather than
publishing an empty release.

Pure stdlib; the CHANGELOG format is the repo's own convention
(``## [X.Y.Z] — title`` headers, newest first).
"""
from __future__ import annotations

import pathlib
import re
import sys

_NEXT_HEADER = re.compile(r"^## \[")


def extract(text: str, version: str) -> str | None:
    """Return the composed release notes for ``version``, or None if absent."""
    header_re = re.compile(r"^## \[" + re.escape(version) + r"\]\s*(.*)$")
    lines = text.splitlines()
    start = None
    title = ""
    for i, line in enumerate(lines):
        m = header_re.match(line)
        if m:
            start = i
            # Strip a leading em-dash / hyphen separator and surrounding space.
            title = m.group(1).strip().lstrip("—-").strip()
            break
    if start is None:
        return None

    body: list[str] = []
    for line in lines[start + 1:]:
        if _NEXT_HEADER.match(line):
            break
        body.append(line)
    # Trim surrounding blank lines from the body.
    while body and not body[0].strip():
        body.pop(0)
    while body and not body[-1].strip():
        body.pop()

    body_text = "\n".join(body)
    if title and body_text:
        return f"**{title}**\n\n{body_text}"
    if title:
        return f"**{title}**"
    return body_text


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: extract_changelog.py <version> [changelog_path]", file=sys.stderr)
        return 2
    version = argv[1]
    path = pathlib.Path(argv[2] if len(argv) > 2 else "CHANGELOG.md")
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"cannot read {path}: {exc}", file=sys.stderr)
        return 2
    notes = extract(text, version)
    if notes is None:
        print(f"no CHANGELOG section for version {version}", file=sys.stderr)
        return 1
    print(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
