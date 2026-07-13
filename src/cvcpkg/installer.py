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
    *,
    max_bytes: int = 4 * 1024 * 1024 * 1024,  # 4 GB
) -> Path:
    """Download *entry*'s archive (or return from cache).

    Tries ``archive_url`` first, then each URL in ``mirror_urls``
    before giving up.  Returns the local path to the verified archive.
    """
    filename = _archive_filename(entry)
    sha = entry.sha256

    if sha and cache_mod.is_cached(cache_dir, sha, filename):
        return cache_mod.cache_path(cache_dir, sha, filename)

    urls: list[str] = []
    if entry.archive_url:
        urls.append(entry.archive_url)
    urls.extend(getattr(entry, "mirror_urls", None) or [])

    if not urls:
        raise InstallError(f"no archive_url for {entry.name}=={entry.version}")

    last_error: Exception | None = None
    for url in urls:
        try:
            path = _download_from_url(url, filename, sha, cache_dir, max_bytes)
            return path
        except (InstallError, IntegrityError) as exc:
            last_error = exc
            import logging

            logging.getLogger("cvcpkg").debug(
                "download from %s failed (%s), trying next mirror...",
                url,
                exc,
            )
            continue

    raise last_error  # type: ignore[misc]


def _download_from_url(
    url: str,
    filename: str,
    sha: str,
    cache_dir: Path,
    max_bytes: int,
) -> Path:
    """Download a single URL, verify SHA-256, store in cache."""
    import tempfile

    from cvcpkg.storage import get_backend

    try:
        backend = get_backend(url)
        info = backend.head(url)
        if info.size >= 0 and info.size > max_bytes:
            raise InstallError(
                f"archive for {filename} is {info.size} bytes, exceeds {max_bytes} limit"
            )
        # Stream to a temp file instead of loading into memory
        cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False, suffix=".download") as tmp:
            total = 0
            h = hashlib.sha256()
            with backend.open(url) as stream:
                while True:
                    chunk = stream.read(1 << 16)  # 64 KB
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        os.unlink(tmp.name)
                        raise InstallError(f"archive for {filename} exceeds {max_bytes} byte limit")
                    h.update(chunk)
                    tmp.write(chunk)
            tmp_path = Path(tmp.name)
    except (InstallError, IntegrityError):
        raise
    except Exception as e:
        raise InstallError(f"failed to download {url}: {e}") from e

    actual = h.hexdigest()
    if sha and actual != sha:
        tmp_path.unlink(missing_ok=True)
        raise IntegrityError(f"sha256 mismatch for {filename}: expected {sha}, got {actual}")

    final_sha = sha or actual
    path = cache_mod.store_from_file(cache_dir, final_sha, filename, tmp_path)
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
    ((".tar.gz", ".tgz"), _extract_tar),
    ((".tar.bz2", ".tbz2"), _extract_tar),
    ((".tar.xz", ".txz"), _extract_tar),
    ((".tar.zst", ".tar.zstd"), _extract_tar),
    ((".tar",), _extract_tar),
    ((".zip",), _extract_zip),
    ((".7z",), _extract_7z),
]


def _sniff_extractor(archive: Path):
    """Sniff *archive*'s magic bytes and return the matching extractor.

    Preferred over filename-extension dispatch because some publishers
    upload archives with a mismatched suffix (e.g. a Windows zip stored
    under a .tar.zst name).
    """
    try:
        with open(archive, "rb") as fh:
            head = fh.read(8)
    except OSError:
        return None
    if head[:2] == b"\x1f\x8b":
        return _extract_tar  # gzip (tarfile handles it)
    if head[:3] == b"BZh":
        return _extract_tar  # bzip2
    if head[:6] == b"\xfd7zXZ\x00":
        return _extract_tar  # xz
    if head[:4] == b"\x50\x4b\x03\x04" or head[:4] == b"\x50\x4b\x05\x06":
        return _extract_zip  # zip (also handles empty-zip end marker)
    if head[:6] == b"\x37\x7a\xbc\xaf\x27\x1c":
        return _extract_7z
    # Plain ustar has "ustar" at offset 257; too deep to sniff from 8B.
    return None


def extract_bundle(archive: Path, prefix: Path) -> None:
    """Extract *archive* into *prefix*, merging into the existing tree.

    Supported formats: .tar.gz, .tgz, .tar.bz2, .tar.xz, .tar.zst,
    .tar, .zip, .7z.  New formats can be added to ``_EXTRACTORS``.

    Dispatch prefers magic-byte sniffing over the filename suffix so
    that archives stored under a mislabeled extension still install.
    """
    prefix.mkdir(parents=True, exist_ok=True)
    sniffed = _sniff_extractor(archive)
    if sniffed is not None:
        sniffed(archive, prefix)
        return
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
    *,
    verify_signatures: bool = False,
    require_signatures: bool = False,
    keys_dir: Path | None = None,
) -> Path:
    """Download, verify, and extract one bundle into *prefix*.

    Signature handling:

    - *require_signatures* — every entry MUST carry a signature and it must
      verify against the trusted keyring; an unsigned entry is a hard error.
      Implies verification.
    - *verify_signatures* — verify the signature when the entry has one, but
      accept unsigned entries (verify-if-present).

    Returns the archive path from the cache.
    """
    archive = download_bundle(entry, cache_dir)

    if require_signatures and not entry.signature:
        raise IntegrityError(
            f"{entry.name}=={entry.version} has no signature, but "
            "--require-signatures was requested"
        )

    if (verify_signatures or require_signatures) and entry.signature:
        from cvcpkg.signing import Signature, SigningError, verify_file

        sig = Signature(
            sig_b64=entry.signature,
            key_fingerprint=entry.key_fingerprint,
        )
        try:
            ki = verify_file(archive, sig, keys_dir)
            print(f"cvcpkg: signature verified for {entry.name} (key: {ki.label})")
        except SigningError as e:
            raise IntegrityError(f"Signature verification failed for {entry.name}: {e}") from e

    extract_bundle(archive, prefix)
    return archive


def build_from_source_fallback(
    name: str,
    prefix: Path,
    *,
    platform: str,
    config: str = "release",
    link: str = "shared",
    recipes_dirs: list[Path] | None = None,
) -> None:
    """Build a component from its recipe as a last-resort fallback.

    Called when no prebuilt binary is available (network error, missing
    package, etc.).  Raises ``InstallError`` if the recipe cannot be
    found or the build fails.
    """
    import logging

    from cvcpkg.builder import BuildError, RecipeError, build_recipe, find_recipes_dir

    log = logging.getLogger("cvcpkg")
    log.info("falling back to source build for %s", name)

    # Locate the recipe directory for this component.
    search_dirs = recipes_dirs or []
    if not search_dirs:
        try:
            search_dirs = [find_recipes_dir()]
        except RecipeError:
            raise InstallError(
                f"no prebuilt binary and no recipes directory found for {name}"
            ) from None

    recipe_dir: Path | None = None
    for rdir in search_dirs:
        candidate = rdir / name
        if (candidate / "recipe.yaml").is_file():
            recipe_dir = candidate
            break

    if recipe_dir is None:
        raise InstallError(f"no prebuilt binary and no recipe found for '{name}'")

    try:
        build_recipe(
            recipe_dir,
            platform=platform,
            config=config,
            link=link,
            prefix=prefix,
        )
    except (BuildError, RecipeError) as exc:
        raise InstallError(f"source build for '{name}' failed: {exc}") from exc


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
