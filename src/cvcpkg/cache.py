# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Content-addressed download cache.

Archives are stored at::

    ~/.cache/cvcpkg/<sha256>/<original-filename>

The cache is shared across all prefixes on the machine.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def default_cache_dir() -> Path:
    """Return the default cache directory, respecting CVCPKG_CACHE."""
    env = os.environ.get("CVCPKG_CACHE")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "cvcpkg"
    return Path.home() / ".cache" / "cvcpkg"


def cache_path(cache_dir: Path, sha256: str, filename: str) -> Path:
    """Return the path where an archive with *sha256* would be cached."""
    return cache_dir / sha256 / filename


def is_cached(cache_dir: Path, sha256: str, filename: str) -> bool:
    """Return True if the archive is already in the cache."""
    p = cache_path(cache_dir, sha256, filename)
    if not p.exists():
        return False
    # Verify hash.
    return file_sha256(p) == sha256


def store(cache_dir: Path, sha256: str, filename: str, data: bytes) -> Path:
    """Write *data* into the cache and return the path."""
    p = cache_path(cache_dir, sha256, filename)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def store_from_file(cache_dir: Path, sha256: str, filename: str, src: Path) -> Path:
    """Move *src* into the cache and return the final path."""
    import shutil

    p = cache_path(cache_dir, sha256, filename)
    p.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(p))
    return p


def file_sha256(path: Path) -> str:
    """Compute the SHA-256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 16):
            h.update(chunk)
    return h.hexdigest()


def gc(cache_dir: Path, referenced_hashes: set[str]) -> int:
    """Remove cached archives not in *referenced_hashes*.

    Returns the number of entries removed.
    """
    if not cache_dir.is_dir():
        return 0
    removed = 0
    for entry in cache_dir.iterdir():
        if entry.is_dir() and entry.name not in referenced_hashes:
            for f in entry.iterdir():
                f.unlink()
            entry.rmdir()
            removed += 1
    return removed
