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


def _safe_extractall(tf: tarfile.TarFile, path: Path) -> None:
    """Extract with filter='data' on Python >=3.12, plain extractall on older."""
    import sys
    if sys.version_info >= (3, 12):
        tf.extractall(path=path, filter="data")
    else:
        tf.extractall(path=path)


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


# ── Archive format registry ──────────────────────────────────────
#
# Maps suffix → extractor function.  To support a new format, add
# an entry to _EXTRACTORS.  Each extractor receives (archive, prefix)
# and must handle path-traversal validation internally.

def _extract_tar(archive: Path, prefix: Path) -> None:
    """Extract any tarball variant that Python's tarfile module supports."""
    with tarfile.open(archive) as tf:
        for member in tf.getmembers():
            if member.name.startswith("/") or ".." in member.name.split("/"):
                raise InstallError(f"unsafe path in archive: {member.name}")
        _safe_extractall(tf, prefix)


def _extract_zip(archive: Path, prefix: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.filename.startswith("/") or ".." in info.filename.split("/"):
                raise InstallError(f"unsafe path in archive: {info.filename}")
        zf.extractall(path=prefix)


def _extract_7z(archive: Path, prefix: Path) -> None:
    """Extract a 7z archive via the system ``7z`` command."""
    import shutil
    import subprocess

    exe = shutil.which("7z") or shutil.which("7za")
    if exe is None:
        raise InstallError(
            "7z not found — install p7zip-full (Linux), 7-zip (Windows), "
            "or p7zip (macOS) to extract .7z archives"
        )
    subprocess.run(
        [exe, "x", str(archive), f"-o{prefix}", "-y"],
        check=True,
        capture_output=True,
    )


# Ordered so the longest (most specific) suffix matches first.
_EXTRACTORS: list[tuple[tuple[str, ...], callable]] = [
    ((".tar.gz", ".tgz"),              _extract_tar),
    ((".tar.bz2", ".tbz2"),            _extract_tar),
    ((".tar.xz", ".txz"),              _extract_tar),
    ((".tar.zst", ".tar.zstd"),         _extract_tar),
    ((".tar",),                         _extract_tar),
    ((".zip",),                         _extract_zip),
    ((".7z",),                          _extract_7z),
]


def extract_bundle(archive: Path, prefix: Path) -> None:
    """Extract *archive* into *prefix*, merging into the existing tree.

    Supported formats: .tar.gz, .tgz, .tar.bz2, .tar.xz, .tar.zst,
    .tar, .zip, .7z.  New formats can be added to ``_EXTRACTORS``.
    """
    prefix.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    for suffixes, extractor in _EXTRACTORS:
        if any(name.endswith(s) for s in suffixes):
            extractor(archive, prefix)
            return
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
