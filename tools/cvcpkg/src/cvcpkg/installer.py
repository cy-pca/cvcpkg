"""Download, verify, and extract bundle archives into a prefix."""

from __future__ import annotations

import hashlib
import os
import tarfile
import zipfile
from pathlib import Path

from cvcpkg import cache as cache_mod
from cvcpkg.errors import InstallError, IntegrityError
from cvcpkg.manifest import CatalogEntry


def download_bundle(
    entry: CatalogEntry,
    cache_dir: Path,
) -> Path:
    """Download *entry*'s archive (or return from cache).

    Returns the local path to the verified archive.
    """
    filename = _archive_filename(entry)
    sha = entry.sha256

    if sha and cache_mod.is_cached(cache_dir, sha, filename):
        return cache_mod.cache_path(cache_dir, sha, filename)

    import urllib.request

    url = entry.archive_url
    if not url:
        raise InstallError(f"no archive_url for {entry.name}=={entry.version}")

    try:
        with urllib.request.urlopen(url, timeout=120) as resp:  # noqa: S310
            data = resp.read()
    except Exception as e:
        raise InstallError(f"failed to download {url}: {e}") from e

    if sha:
        actual = hashlib.sha256(data).hexdigest()
        if actual != sha:
            raise IntegrityError(
                f"sha256 mismatch for {filename}: expected {sha}, got {actual}"
            )

    path = cache_mod.store(cache_dir, sha or hashlib.sha256(data).hexdigest(), filename, data)
    return path


def extract_bundle(archive: Path, prefix: Path) -> None:
    """Extract *archive* into *prefix*, merging into the existing tree."""
    prefix.mkdir(parents=True, exist_ok=True)

    if archive.name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive, "r:gz") as tf:
            # Security: prevent path traversal
            for member in tf.getmembers():
                if member.name.startswith("/") or ".." in member.name.split("/"):
                    raise InstallError(f"unsafe path in archive: {member.name}")
            tf.extractall(path=prefix, filter="data")
    elif archive.name.endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                if info.filename.startswith("/") or ".." in info.filename.split("/"):
                    raise InstallError(f"unsafe path in archive: {info.filename}")
            zf.extractall(path=prefix)
    elif archive.name.endswith((".tar.zst", ".tar.xz", ".tar.bz2")):
        with tarfile.open(archive) as tf:
            for member in tf.getmembers():
                if member.name.startswith("/") or ".." in member.name.split("/"):
                    raise InstallError(f"unsafe path in archive: {member.name}")
            tf.extractall(path=prefix, filter="data")
    else:
        raise InstallError(f"unsupported archive format: {archive.name}")


def install_entry(
    entry: CatalogEntry,
    prefix: Path,
    cache_dir: Path,
) -> Path:
    """Download, verify, and extract one bundle into *prefix*.

    Returns the archive path from the cache.
    """
    archive = download_bundle(entry, cache_dir)
    extract_bundle(archive, prefix)
    return archive


def _archive_filename(entry: CatalogEntry) -> str:
    """Derive the expected archive filename from a catalog entry."""
    if entry.archive_url:
        return entry.archive_url.rsplit("/", 1)[-1]
    # Fallback: construct from metadata.
    parts = ["libcvc-deps", entry.name, entry.version, entry.platform, entry.arch, entry.build_type]
    if entry.link:
        parts.append(entry.link)
    ext = ".tar.gz" if entry.platform != "windows" else ".zip"
    return "-".join(parts) + ext
