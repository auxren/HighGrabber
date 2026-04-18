"""ZIP extraction with path-traversal protection.

Hightail concert archives are almost always ZIPs. We extract each into a
subdirectory named after the ZIP (sans extension), so overlapping filenames
between archives don't collide.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(slots=True)
class ExtractResult:
    zip_path: Path
    out_dir: Path
    status: str  # "done" | "skip" | "fail"
    error: Optional[str] = None


def _safe_join(root: Path, member: str) -> Optional[Path]:
    """Return root/member iff it stays under root, else None (zip-slip guard)."""
    candidate = (root / member).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate


def extract_zip(zip_path: Path, out_dir: Optional[Path] = None) -> ExtractResult:
    if out_dir is None:
        out_dir = zip_path.with_suffix("")
    if out_dir.exists() and any(out_dir.iterdir()):
        return ExtractResult(zip_path=zip_path, out_dir=out_dir, status="skip")
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            for info in zf.infolist():
                if info.is_dir():
                    target = _safe_join(out_dir, info.filename)
                    if target is None:
                        continue
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target = _safe_join(out_dir, info.filename)
                if target is None:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, target.open("wb") as dst:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        dst.write(chunk)
    except (zipfile.BadZipFile, OSError) as exc:
        return ExtractResult(zip_path=zip_path, out_dir=out_dir, status="fail", error=str(exc))
    return ExtractResult(zip_path=zip_path, out_dir=out_dir, status="done")


def is_zip(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            return fh.read(4) == b"PK\x03\x04"
    except OSError:
        return False
