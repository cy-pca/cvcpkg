"""Content-addressed build cache for cvcpkg.

Caches per-recipe build artifacts keyed by chain_hash — a transitive
SHA-256 digest covering the recipe, its build scripts, patches, shared
helper scripts, and all dependency chain hashes.

Layout::

    <cache_dir>/
        <chain_hash>-<platform>-<arch>-<config>-<link>/
            meta.json          # metadata: recipe name, version, timestamps
            install.tar.gz     # tarball of the install directory

The cache directory defaults to ``~/.cache/cvcpkg/builds/`` and can be
overridden via the ``CVCPKG_BUILD_CACHE`` environment variable.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tarfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

# ── Cache key ───────────────────────────────────────────────────


def cache_key(
    chain_hash: str,
    platform: str,
    arch: str,
    config: str,
    link: str,
) -> str:
    """Build the composite cache key for a recipe build.

    The key uniquely identifies a build variant: same recipe content on
    the same platform/arch/config/link always maps to the same slot.
    """
    return f"{chain_hash}-{platform}-{arch}-{config}-{link}"


# ── Metadata ────────────────────────────────────────────────────


@dataclass
class CacheEntryMeta:
    """Metadata stored alongside a cached build artifact."""

    name: str
    version: str
    chain_hash: str
    platform: str
    arch: str
    config: str
    link: str
    archive_sha256: str
    archive_size_bytes: int
    stored_at: str = ""  # ISO-8601 timestamp
    last_used_at: str = ""  # ISO-8601 timestamp (updated on lookup hit)
    org: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, text: str) -> CacheEntryMeta:
        data = json.loads(text)
        # Accept extra keys gracefully (forward compat).
        known = {f.name for f in field()} if False else set(cls.__dataclass_fields__)
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


# ── Default directory ───────────────────────────────────────────


def default_build_cache_dir() -> Path:
    """Return the build cache directory.

    Checks ``CVCPKG_BUILD_CACHE`` then ``XDG_CACHE_HOME``, falling
    back to ``~/.cache/cvcpkg/builds``.
    """
    env = os.environ.get("CVCPKG_BUILD_CACHE")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "cvcpkg" / "builds"
    return Path.home() / ".cache" / "cvcpkg" / "builds"


# ── BuildCache ──────────────────────────────────────────────────

_ARCHIVE_NAME = "install.tar.gz"
_META_NAME = "meta.json"


class BuildCache:
    """Content-addressed local build cache.

    Each entry is a directory named by its composite cache key containing
    a tarball of the recipe's install directory and a metadata file.
    """

    def __init__(self, cache_dir: Path | None = None) -> None:
        self._dir = cache_dir if cache_dir is not None else default_build_cache_dir()

    @property
    def cache_dir(self) -> Path:
        return self._dir

    # ── lookup ──────────────────────────────────────────────────

    def lookup(
        self,
        chain_hash_val: str,
        platform: str,
        arch: str,
        config: str,
        link: str,
    ) -> Path | None:
        """Return the path to the cached archive if it exists, else None.

        Also updates ``last_used_at`` on a hit.
        """
        key = cache_key(chain_hash_val, platform, arch, config, link)
        entry_dir = self._dir / key
        archive = entry_dir / _ARCHIVE_NAME
        meta_path = entry_dir / _META_NAME
        if not archive.is_file() or not meta_path.is_file():
            return None

        # Verify integrity: archive sha256 matches meta.
        try:
            meta = CacheEntryMeta.from_json(meta_path.read_text())
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

        actual_sha = _sha256_file(archive)
        if actual_sha != meta.archive_sha256:
            # Corrupted entry — remove it.
            shutil.rmtree(entry_dir, ignore_errors=True)
            return None

        # Update last_used_at.
        meta.last_used_at = _now_iso()
        meta_path.write_text(meta.to_json())

        return archive

    # ── store ───────────────────────────────────────────────────

    def store(
        self,
        install_dir: Path,
        name: str,
        version: str,
        chain_hash_val: str,
        platform: str,
        arch: str,
        config: str,
        link: str,
        org: str = "",
    ) -> Path:
        """Archive *install_dir* into the cache and return the archive path."""
        key = cache_key(chain_hash_val, platform, arch, config, link)
        entry_dir = self._dir / key
        entry_dir.mkdir(parents=True, exist_ok=True)

        archive = entry_dir / _ARCHIVE_NAME
        _create_tarball(install_dir, archive)

        sha = _sha256_file(archive)
        size = archive.stat().st_size
        now = _now_iso()

        meta = CacheEntryMeta(
            name=name,
            version=version,
            chain_hash=chain_hash_val,
            platform=platform,
            arch=arch,
            config=config,
            link=link,
            archive_sha256=sha,
            archive_size_bytes=size,
            stored_at=now,
            last_used_at=now,
            org=org,
        )
        (entry_dir / _META_NAME).write_text(meta.to_json())
        return archive

    # ── restore ─────────────────────────────────────────────────

    def restore(self, archive: Path, target_dir: Path) -> None:
        """Extract a cached archive into *target_dir*."""
        target_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(target_dir, filter="data")

    # ── evict ───────────────────────────────────────────────────

    def evict(
        self,
        chain_hash_val: str,
        platform: str,
        arch: str,
        config: str,
        link: str,
    ) -> bool:
        """Remove a single cache entry.  Returns True if it existed."""
        key = cache_key(chain_hash_val, platform, arch, config, link)
        entry_dir = self._dir / key
        if entry_dir.is_dir():
            shutil.rmtree(entry_dir)
            return True
        return False

    # ── list ────────────────────────────────────────────────────

    def list_entries(self) -> list[CacheEntryMeta]:
        """Return metadata for all cache entries, sorted by stored_at."""
        entries: list[CacheEntryMeta] = []
        if not self._dir.is_dir():
            return entries
        for child in sorted(self._dir.iterdir()):
            meta_path = child / _META_NAME
            if child.is_dir() and meta_path.is_file():
                try:
                    entries.append(CacheEntryMeta.from_json(meta_path.read_text()))
                except (json.JSONDecodeError, TypeError, KeyError):
                    continue
        entries.sort(key=lambda e: e.stored_at)
        return entries

    # ── info ────────────────────────────────────────────────────

    def info(
        self,
        chain_hash_val: str,
        platform: str,
        arch: str,
        config: str,
        link: str,
    ) -> CacheEntryMeta | None:
        """Return metadata for a specific entry, or None."""
        key = cache_key(chain_hash_val, platform, arch, config, link)
        meta_path = self._dir / key / _META_NAME
        if not meta_path.is_file():
            return None
        try:
            return CacheEntryMeta.from_json(meta_path.read_text())
        except (json.JSONDecodeError, TypeError, KeyError):
            return None

    # ── purge ───────────────────────────────────────────────────

    def purge(
        self,
        *,
        max_size_bytes: int | None = None,
        max_age_seconds: float | None = None,
    ) -> int:
        """Remove entries exceeding size or age limits.

        When *max_size_bytes* is given, the oldest entries (by
        ``last_used_at``) are evicted until total size is within limit.

        When *max_age_seconds* is given, entries not used within that
        window are evicted.

        Returns the number of entries removed.
        """
        if not self._dir.is_dir():
            return 0

        entries = self._list_entries_with_size()
        removed = 0

        # Age-based eviction.
        if max_age_seconds is not None:
            cutoff = time.time() - max_age_seconds
            surviving: list[tuple[CacheEntryMeta, int, Path]] = []
            for meta, size, path in entries:
                ts = _parse_iso(meta.last_used_at or meta.stored_at)
                if ts < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
                    removed += 1
                else:
                    surviving.append((meta, size, path))
            entries = surviving

        # Size-based eviction (LRU).
        if max_size_bytes is not None:
            # Sort by last_used_at ascending (oldest first for eviction).
            entries.sort(key=lambda e: _parse_iso(e[0].last_used_at or e[0].stored_at))
            total = sum(s for _, s, _ in entries)
            while total > max_size_bytes and entries:
                _, size, path = entries.pop(0)
                shutil.rmtree(path, ignore_errors=True)
                total -= size
                removed += 1

        return removed

    # ── total_size ──────────────────────────────────────────────

    def total_size_bytes(self) -> int:
        """Return total size of all cached archives in bytes."""
        if not self._dir.is_dir():
            return 0
        return sum(s for _, s, _ in self._list_entries_with_size())

    # ── internal helpers ────────────────────────────────────────

    def _list_entries_with_size(
        self,
    ) -> list[tuple[CacheEntryMeta, int, Path]]:
        """List entries with their archive size and directory path."""
        result: list[tuple[CacheEntryMeta, int, Path]] = []
        if not self._dir.is_dir():
            return result
        for child in self._dir.iterdir():
            meta_path = child / _META_NAME
            archive = child / _ARCHIVE_NAME
            if child.is_dir() and meta_path.is_file() and archive.is_file():
                try:
                    meta = CacheEntryMeta.from_json(meta_path.read_text())
                    size = archive.stat().st_size
                    result.append((meta, size, child))
                except (json.JSONDecodeError, TypeError, KeyError, OSError):
                    continue
        return result

    def purge_stale(self, valid_chain_hashes: set[str]) -> int:
        """Remove entries whose chain_hash is not in *valid_chain_hashes*.

        Returns the number of entries removed.
        """
        if not self._dir.is_dir():
            return 0
        removed = 0
        for meta, _size, path in self._list_entries_with_size():
            if meta.chain_hash not in valid_chain_hashes:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        return removed


# ── Private helpers ─────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _create_tarball(source_dir: Path, dest: Path) -> None:
    """Create a gzipped tar of *source_dir* contents (not the dir itself)."""
    with tarfile.open(dest, "w:gz") as tar:
        for child in sorted(source_dir.rglob("*")):
            arcname = child.relative_to(source_dir)
            tar.add(child, arcname=str(arcname))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str) -> float:
    """Parse an ISO-8601 timestamp to a Unix epoch float."""
    if not s:
        return 0.0
    try:
        return datetime.fromisoformat(s).timestamp()
    except (ValueError, TypeError):
        return 0.0
