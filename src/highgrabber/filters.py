"""Filename / existence filters for the download pipeline.

Two stages run before files are queued for download:

1. Pattern filtering — `--include` / `--exclude` regex applied to filenames.
2. Existence filtering — recursively scan the destination tree and skip files
   whose basename, zip-stem, or embedded YYYY-MM-DD date already appears.
   The date signal is the load-bearing one for libraries with mixed naming
   conventions (e.g. Phish bootleg archives).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .api import HightailFile


_DATE_RE = re.compile(
    r"(?<!\d)(?:19|20)\d{2}[-_./ ]?(?:0[1-9]|1[0-2])[-_./ ]?(?:0[1-9]|[12]\d|3[01])(?!\d)"
)


def extract_date(s: str) -> Optional[str]:
    """Return the first YYYY-MM-DD-shaped date in `s`, normalized, else None."""
    m = _DATE_RE.search(s)
    if not m:
        return None
    digits = re.sub(r"\D", "", m.group(0))
    if len(digits) != 8:
        return None
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def _strip_archive_ext(name: str) -> str:
    n = name.lower()
    for ext in (".tar.gz", ".tar.bz2", ".tar.xz"):
        if n.endswith(ext):
            return n[: -len(ext)]
    for ext in (".zip", ".tar", ".rar", ".7z"):
        if n.endswith(ext):
            return n[: -len(ext)]
    return n


@dataclass(slots=True)
class ExistingIndex:
    files: set[str] = field(default_factory=set)
    dirs: set[str] = field(default_factory=set)
    dates: set[str] = field(default_factory=set)


def scan_existing(dest: Path) -> ExistingIndex:
    """Walk dest recursively, indexing basenames, dir names, and embedded dates."""
    idx = ExistingIndex()
    if not dest.exists():
        return idx
    for p in dest.rglob("*"):
        try:
            is_dir = p.is_dir()
        except OSError:
            continue
        name = p.name.lower()
        if is_dir:
            idx.dirs.add(name)
        else:
            idx.files.add(name)
        d = extract_date(name)
        if d:
            idx.dates.add(d)
    return idx


def existence_skip_reason(f: HightailFile, idx: ExistingIndex) -> Optional[str]:
    """Short reason if `f` should be skipped due to existing files, else None."""
    name_lc = f.name.lower()
    if name_lc in idx.files:
        return "name match"
    stem = _strip_archive_ext(name_lc)
    if stem != name_lc and stem in idx.dirs:
        return "extracted-dir match"
    d = extract_date(name_lc)
    if d and d in idx.dates:
        return f"date match ({d})"
    return None


def compile_pattern_filter(
    include: Optional[str], exclude: Optional[str]
) -> Callable[[HightailFile], Optional[str]]:
    """Return fn(file) → None if it passes, else a short reason for rejection."""
    inc = re.compile(include, re.IGNORECASE) if include else None
    exc = re.compile(exclude, re.IGNORECASE) if exclude else None

    def check(f: HightailFile) -> Optional[str]:
        if inc and not inc.search(f.name):
            return "no --include match"
        if exc and exc.search(f.name):
            return "matched --exclude"
        return None

    return check
