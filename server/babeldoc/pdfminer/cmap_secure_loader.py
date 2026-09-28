"""Integrity-locked loader for bundled CMap pickle files (GHSA-m8gf-v64p-gfmg).

The legacy loaders built a filesystem path from a PDF-controlled CMap name and
pickle.loads()'d it, allowing absolute-path injection / `..` traversal to an
attacker-placed file. This loader instead deserializes ONLY a file that is:

  1. listed by exact filename in the pinned manifest (allowlist),
  2. resolved inside the bundled cmap directory (containment), and
  3. byte-for-byte matching the pinned sha256 + size (integrity).

CMAP_PATH and any external search directory are intentionally dropped. The
sha256 is computed over the on-disk .gz bytes before decompression, so a
tampered or oversized file never reaches gzip/pickle.
"""

from __future__ import annotations

import gzip
import hashlib
import pickle
from pathlib import Path
from typing import Any

from babeldoc.pdfminer._cmap_manifest_data import CMAP_MANIFEST

BUNDLED_CMAP_DIR = (Path(__file__).resolve().parent / "cmap").resolve()


class CMapIntegrityError(Exception):
    """Raised when a requested CMap is not a verified bundled file."""


def load_verified_cmap_data(name: str) -> Any:
    """Return the unpickled namespace dict for bundled CMap `name`, or raise.

    Raises CMapIntegrityError on unknown name, path escape, missing file, or
    size/sha mismatch. Callers map this to their own CMapNotFound.
    """
    clean = name.replace("\0", "")
    filename = f"{clean}.pickle.gz"

    pinned = CMAP_MANIFEST.get(filename)
    if pinned is None:
        raise CMapIntegrityError(f"unknown cmap: {clean!r}")
    expected_sha, expected_size = pinned

    path = (BUNDLED_CMAP_DIR / filename).resolve()
    if path.parent != BUNDLED_CMAP_DIR:
        raise CMapIntegrityError(f"path escapes bundled dir: {clean!r}")
    try:
        # Bound the read even if a file changes between checking and reading.
        with path.open("rb") as stream:
            raw = stream.read(expected_size + 1)
    except OSError as exc:
        raise CMapIntegrityError(f"unreadable bundled cmap: {clean!r}") from exc
    if len(raw) != expected_size:
        raise CMapIntegrityError(f"size mismatch: {clean!r}")
    if hashlib.sha256(raw).hexdigest() != expected_sha:
        raise CMapIntegrityError(f"sha256 mismatch: {clean!r}")

    # Safe: bytes verified against pinned sha256 + size above; only ever a
    # bundled, integrity-checked file reaches this point.
    return pickle.loads(gzip.decompress(raw))  # noqa: S301
