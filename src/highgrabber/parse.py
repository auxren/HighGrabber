"""Extract Hightail `receive` slugs from arbitrary input (URLs, files, prose)."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Iterable

SLUG_RE = re.compile(
    r"https?://spaces\.hightail\.com/receive/([A-Za-z0-9]+)",
    re.IGNORECASE,
)


def extract_slugs(text: str) -> list[str]:
    """Return ordered, de-duplicated slugs found anywhere in `text`."""
    seen: set[str] = set()
    out: list[str] = []
    for m in SLUG_RE.finditer(text):
        slug = m.group(1)
        if slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def collect_slugs(inputs: Iterable[str]) -> list[str]:
    """Collect slugs from a mixed list of URLs, file paths, or `-` for stdin.

    Each input may be:
      - a full Hightail URL
      - a path to a text file containing links (anywhere in the prose)
      - `-` to read from stdin
    Unrecognized strings are ignored with a warning to stderr.
    """
    seen: set[str] = set()
    out: list[str] = []

    def _add_many(slugs: Iterable[str]) -> None:
        for s in slugs:
            if s not in seen:
                seen.add(s)
                out.append(s)

    for item in inputs:
        if item == "-":
            _add_many(extract_slugs(sys.stdin.read()))
            continue
        if "hightail.com/receive/" in item:
            _add_many(extract_slugs(item))
            continue
        p = Path(item).expanduser()
        if p.is_file():
            try:
                _add_many(extract_slugs(p.read_text(errors="replace")))
            except OSError as exc:
                print(f"warning: cannot read {p}: {exc}", file=sys.stderr)
            continue
        print(f"warning: not a URL or readable file, ignored: {item!r}", file=sys.stderr)
    return out
