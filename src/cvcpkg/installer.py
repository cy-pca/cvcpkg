# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Download, verify, and extract bundle archives into a prefix."""

from __future__ import annotations

import hashlib
import logging
import os
import tarfile
import time
import zipfile
from collections.abc import Callable, Collection, MutableMapping
from pathlib import Path
from typing import TYPE_CHECKING

from cvcpkg import cache as cache_mod
from cvcpkg._archive import archive_format, materialize_symlinks, open_tar
from cvcpkg.errors import DownloadIntegrityError, InstallError, IntegrityError
from cvcpkg.manifest import CatalogEntry
from cvcpkg.retry import backoff_delay, with_retry

if TYPE_CHECKING:
    from cvcpkg.builder import LicenseAcceptance


def _safe_extractall(tf: tarfile.TarFile, path: Path) -> None:
    """Extract with filter='data' on Python >=3.12, plain extractall on older.

    Guard a broken CPython point release (seen on GitHub Actions hostedtoolcache
    3.12.14) whose tarfile filters call
    ``os.path.realpath(..., strict=os.path.ALLOW_MISSING)`` while its ``posixpath``
    never defines ``ALLOW_MISSING`` -> ``AttributeError``. Both the 'data' and 'tar'
    filters share the broken ``_get_filtered_attrs``, so neither runs. Each bundle's
    sha256 is already verified before extraction (download_bundle), so the filter is
    defense-in-depth; on such an interpreter apply the essential checks ourselves
    (no absolute paths, no ``..`` traversal, no escaping link targets) and extract
    fully-trusted rather than crash the whole install. The AttributeError fires in
    the per-member filter before anything is written, so re-extracting is clean.
    """
    import sys

    if sys.version_info < (3, 12):
        tf.extractall(path=path)
        return
    try:
        tf.extractall(path=path, filter="data")
    except AttributeError as exc:
        if "ALLOW_MISSING" not in str(exc):
            raise
        # Both stdlib filters share _get_filtered_attrs, which is what calls the
        # broken os.path.ALLOW_MISSING realpath — so 'tar' fails the same way. Do
        # the essential tar-filter checks ourselves (no absolute paths, no '..'
        # traversal, no absolute/escaping link targets), then extract fully-trusted:
        # the bundle is already sha256-verified (download_bundle), so this restores
        # path-traversal safety without touching the broken interpreter path.
        logging.getLogger(__name__).warning(
            "tarfile filters are unusable on this interpreter (%s: %s); extracting "
            "with a manual path-traversal guard",
            sys.version.split()[0],
            exc,
        )
        for m in tf.getmembers():
            for label, target in (("path", m.name), ("link target", m.linkname)):
                if not target and label == "link target":
                    continue
                if (
                    target.startswith(("/", "\\"))
                    or os.path.isabs(target)
                    or ".." in Path(target).parts
                ):
                    raise InstallError(f"unsafe {label} in bundle archive: {target!r}") from None
        tf.extractall(path=path, filter="fully_trusted")


def download_bundle(
    entry: CatalogEntry,
    cache_dir: Path,
    *,
    max_bytes: int = 4 * 1024 * 1024 * 1024,  # 4 GB
) -> Path:
    """Download *entry*'s archive (or return from cache).

    Tries ``archive_url`` first, then each URL in ``mirror_urls``
    before giving up.  Each URL gets the transfer and integrity retries of
    :func:`_download_verified`.  Returns the local path to the verified
    archive; nothing unverified is ever cached or returned.
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
            return _download_verified(url, filename, sha, cache_dir, max_bytes)
        except (InstallError, IntegrityError) as exc:
            last_error = exc
            logging.getLogger("cvcpkg").debug(
                "download from %s failed (%s), trying next mirror...",
                url,
                exc,
            )
            continue

    raise last_error  # type: ignore[misc]


# ── Download retry policy ─────────────────────────────────────────
#
# Two budgets, for two different failures.
#
# The TRANSFER budget is with_retry's, for transport faults: 502/503/504,
# resets, and a body cut off short of its Content-Length (which the https
# backend raises as TruncatedBody).  with_retry's defaults -- four attempts
# inside about four seconds -- are sized for a proxy blip.  The outage that
# matters to a whole-archive download is a deploy replacing the server's
# container: on 2026-10-12 cvcpkg.org was recreated at 16:35:38 and healthy at
# 16:36:54, and a job that hit the gap failed with a bare HTTP error.  So this
# window spans about two minutes; it is still bounded by attempts and by wall
# clock, and a fully-down server already fails the catalog fetch before this.
_TRANSFER_ATTEMPTS = 10
_TRANSFER_BASE_DELAY_S = 2.0
_TRANSFER_MAX_DELAY_S = 20.0
_TRANSFER_BUDGET_S = 150.0

# The INTEGRITY budget: how many times one URL is downloaded, each time from
# scratch, when a complete body does not hash to the catalog's sha256.  A
# mismatch usually means the server really serves other bytes, so this stays
# small; but a body can also be mangled in transit, or served by a server in
# the middle of being replaced, and one more fetch after a pause settles which.
# Nothing is resumed: each attempt starts at byte 0 into a new temp file, and
# the rejected file is deleted before the next one starts.
INTEGRITY_ATTEMPTS = 3
_INTEGRITY_BASE_DELAY_S = 5.0
_INTEGRITY_MAX_DELAY_S = 20.0


def _download_verified(
    url: str,
    filename: str,
    sha: str,
    cache_dir: Path,
    max_bytes: int,
) -> Path:
    """Download *url* into the cache, retrying until its bytes verify.

    Raises :class:`DownloadIntegrityError` (an ``IntegrityError``) after
    ``INTEGRITY_ATTEMPTS`` complete bodies have all hashed wrong, and the last
    ``InstallError`` if the transfer itself never succeeds.  Either error
    names the number of downloads that were attempted.
    """
    log = logging.getLogger("cvcpkg")
    tries = 0

    def _attempt() -> Path:
        nonlocal tries
        tries += 1
        return _download_from_url(url, filename, sha, cache_dir, max_bytes)

    mismatches: list[DownloadIntegrityError] = []
    for round_ in range(INTEGRITY_ATTEMPTS):
        try:
            # Retry the entire transfer, not just connection setup: a stream
            # that fails after a 200 (proxy reset, incomplete read) is only
            # recoverable by starting the file again.
            return with_retry(
                _attempt,
                what=f"download {filename}",
                attempts=_TRANSFER_ATTEMPTS,
                budget_s=_TRANSFER_BUDGET_S,
                base_delay_s=_TRANSFER_BASE_DELAY_S,
                max_delay_s=_TRANSFER_MAX_DELAY_S,
            )
        except DownloadIntegrityError as exc:
            mismatches.append(exc)
            if round_ + 1 == INTEGRITY_ATTEMPTS:
                break
            wait = backoff_delay(round_, _INTEGRITY_BASE_DELAY_S, _INTEGRITY_MAX_DELAY_S)
            # A warning, not debug: a mismatch that a second download cures is
            # still evidence of a server or network problem worth seeing in CI.
            log.warning(
                "%s -- discarded; downloading again from scratch in %.1fs (attempt %d/%d)",
                exc,
                wait,
                round_ + 2,
                INTEGRITY_ATTEMPTS,
            )
            time.sleep(wait)
        except InstallError as exc:
            if tries > 1:
                raise InstallError(f"{exc} (gave up after {tries} download attempts)") from exc
            raise

    last = mismatches[-1]
    raise DownloadIntegrityError(
        filename,
        last.expected,
        last.actual,
        url=last.url,
        status=last.status,
        content_length=last.content_length,
        received=last.received,
        attempts=tries,
        actuals=tuple(m.actual for m in mismatches),
    )


def _download_from_url(
    url: str,
    filename: str,
    sha: str,
    cache_dir: Path,
    max_bytes: int,
) -> Path:
    """Download a single URL, verify SHA-256, store in cache.

    Only a body that verifies ever reaches the cache: the bytes are written to
    a temp file beside it, which is deleted on every failure (cut connection,
    size cap, hash mismatch) and moved into place only after the hash matches.
    """
    import tempfile

    from cvcpkg.config import authorize_request
    from cvcpkg.storage import get_backend

    # A private org's archive is downloadable only by an authenticated member
    # (the server 404s otherwise).  Authenticate ONLY to our own origin; for a
    # public bundle whose archive_url / mirror_url points at GitHub Releases or a
    # CDN this leaves the URL unchanged and sends no header.  For our own server
    # this also upgrades the http:// URL the proxy emits back to https:// so the
    # token is never sent in cleartext.
    url, auth = authorize_request(url)

    tmp_path: Path | None = None
    status: int | None = None
    content_length: int | None = None
    total = 0
    try:
        backend = get_backend(url)
        # The size probe is an OPTIMISATION, not a gate: it lets an oversized
        # archive be rejected before a byte is transferred. It must never be
        # able to fail the download on its own -- a transient 5xx on the probe
        # used to raise InstallError, and since catalog entries carry a single
        # archive_url with no mirrors, that killed the whole install. The
        # streaming cap below is the real enforcement and needs no probe.
        # (catalog._fetch_url learned this after an outage; this path did not.)
        try:
            info = backend.head(url, headers=auth) if auth else backend.head(url)
        except Exception as probe_exc:  # noqa: BLE001 -- advisory only
            logging.getLogger("cvcpkg").debug(
                "size probe for %s failed (%s); relying on the streaming cap", url, probe_exc
            )
        else:
            if info.size >= 0 and info.size > max_bytes:
                raise InstallError(
                    f"archive for {filename} is {info.size} bytes, exceeds {max_bytes} limit"
                )
        # Stream to a temp file instead of loading into memory
        cache_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=cache_dir, delete=False, suffix=".download") as tmp:
            tmp_path = Path(tmp.name)
            h = hashlib.sha256()
            with backend.open(url, headers=auth) if auth else backend.open(url) as stream:
                # Diagnostics for a mismatch report; the http(s) body exposes
                # both, any other backend's stream simply has neither.
                status = getattr(stream, "status", None)
                content_length = _header_int(getattr(stream, "headers", None), "Content-Length")
                while True:
                    # An http(s) body that ends short of its Content-Length
                    # raises TruncatedBody here (a transient fault, retried by
                    # the caller) rather than returning b"" as if complete.
                    chunk = stream.read(1 << 16)  # 64 KB
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise InstallError(f"archive for {filename} exceeds {max_bytes} byte limit")
                    h.update(chunk)
                    tmp.write(chunk)

        actual = h.hexdigest()
        if sha and actual != sha:
            raise DownloadIntegrityError(
                filename,
                sha,
                actual,
                url=url,
                status=status,
                content_length=content_length,
                received=total,
            )

        final_sha = sha or actual
        return cache_mod.store_from_file(cache_dir, final_sha, filename, tmp_path)
    except BaseException as e:
        # Never leave a partial or rejected body behind: it is not in the
        # cache's <sha>/ layout so nothing would install it, but it would
        # accumulate in the cache directory forever.
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        if isinstance(e, (InstallError, IntegrityError)) or not isinstance(e, Exception):
            raise
        raise InstallError(f"failed to download {url}: {e}") from e


def _header_int(headers: object, name: str) -> int | None:
    """Integer value of header *name*, or None when absent or malformed."""
    get = getattr(headers, "get", None)
    if get is None:
        return None
    try:
        value = get(name)
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ── Archive extractors ───────────────────────────────────────────
#
# One per container format, chosen by _archive.archive_format (magic bytes,
# then suffix).  Each receives (archive, prefix) and must handle
# path-traversal validation internally.


def _extract_tar(archive: Path, prefix: Path) -> list[str]:
    """Extract any tar flavour ``_archive.open_tar`` can open; return member names."""
    with open_tar(archive) as tf:
        members = tf.getmembers()
        for member in members:
            if member.name.startswith("/") or ".." in member.name.split("/"):
                raise InstallError(f"unsafe path in archive: {member.name}")
        _safe_extractall(tf, prefix)
        materialize_symlinks(tf, prefix)
        return [m.name for m in members]


def _extract_zip(archive: Path, prefix: Path) -> list[str]:
    """Extract a zip; return member names."""
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        for info in infos:
            if info.filename.startswith("/") or ".." in info.filename.split("/"):
                raise InstallError(f"unsafe path in archive: {info.filename}")
        zf.extractall(path=prefix)
        return [i.filename for i in infos]


def _extract_7z(archive: Path, prefix: Path) -> None:
    """Extract a 7z archive via the system ``7z`` command (member names unknown)."""
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


# Every tar flavour goes through _extract_tar.
_EXTRACTORS = {"zip": _extract_zip, "7z": _extract_7z}


def extract_bundle(archive: Path, prefix: Path) -> list[str] | None:
    """Extract *archive* into *prefix*, merging into the existing tree.

    Supported formats: tar (plain, gzip, bzip2, xz; zstd on Python 3.14+),
    zip, 7z.  The format comes from the archive's magic bytes, falling back to
    the filename suffix only for content with no signature, so archives stored
    under a mislabeled extension still install -- every Windows bundle on
    cvcpkg.org is a zip under a ``.tar.zst`` name.

    Returns the archive's member names, or ``None`` when the extractor cannot
    list them (7z).
    """
    prefix.mkdir(parents=True, exist_ok=True)
    fmt = archive_format(archive)
    return _EXTRACTORS.get(fmt, _extract_tar)(archive, prefix)


# ── Bundle manifests ─────────────────────────────────────────────
#
# A bundle's manifest belongs at share/libcvc-deps/<name>/manifest.yaml, the
# layout stage_bundle has used since 2026-08-21 so co-installed bundles do not
# overwrite each other's manifest.  Bundles published earlier ship it flat at
# share/libcvc-deps/manifest.yaml, where each extraction overwrites the last,
# and a few ship none.  The installer settles every bundle into the per-name
# slot, so verify, repair and uninstall can find each one.

_META_DIR = ("share", "libcvc-deps")
_FLAT_MANIFEST = "share/libcvc-deps/manifest.yaml"


def _member_path(name: str) -> str:
    """An archive member name as a prefix-relative POSIX path."""
    p = name.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/")


def _flat_manifest_owner(flat: Path) -> str | None:
    """The ``bundle.name`` of a legacy flat manifest, if it is a usable one."""
    import yaml

    try:
        data = yaml.safe_load(flat.read_text())
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("bundle"), dict):
        return None
    name = data["bundle"].get("name")
    if not isinstance(name, str) or not name or name in (".", ".."):
        return None
    if "/" in name or "\\" in name:
        return None
    return name


def _pin_flat_manifest(prefix: Path) -> None:
    """Copy a legacy flat manifest to its owner's per-name slot.

    Run before extracting another bundle, which may overwrite the flat file:
    without this, the bundle that owned it would be left with no manifest.
    """
    import shutil

    meta = prefix.joinpath(*_META_DIR)
    flat = meta / "manifest.yaml"
    if not flat.is_file():
        return
    owner = _flat_manifest_owner(flat)
    if owner is None:
        return
    dest = meta / owner / "manifest.yaml"
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(flat, dest)


def _synthesized_manifest(entry: CatalogEntry) -> dict:
    """A minimal manifest for a bundle that shipped none, from its catalog entry."""
    return {
        "schema_version": 3,
        "bundle": {
            "name": entry.name,
            "version": entry.version,
            "upstream_version": entry.upstream_version,
            "cvc_revision": entry.cvc_revision,
            "platform": entry.platform,
            "arch": entry.arch,
            "build_type": entry.build_type,
            "link": entry.link,
        },
        "dependencies": {
            "required": [
                {"name": d.name, "version": d.version} if d.version else {"name": d.name}
                for d in entry.required_deps
            ],
        },
        "integrity": {"sha256": entry.sha256, "size_bytes": entry.size_bytes},
        "meta": {
            "synthesized": True,
            "description": "Written by cvcpkg install: the bundle archive carried no manifest.",
        },
    }


def settle_bundle_manifest(entry: CatalogEntry, members: list[str] | None, prefix: Path) -> Path:
    """Make sure *entry* has its manifest at ``share/libcvc-deps/<name>/``.

    *members* is what the archive just extracted.  A per-name manifest among
    them is left alone.  A flat one written by this bundle is copied into the
    per-name slot.  Otherwise a manifest is synthesized from the catalog entry.
    With *members* unknown (7z), an existing per-name manifest is trusted.
    Returns the per-name path.
    """
    import shutil

    import yaml

    meta = prefix.joinpath(*_META_DIR)
    dest = meta / entry.name / "manifest.yaml"
    per_name_rel = f"share/libcvc-deps/{entry.name}/manifest.yaml"

    names = None if members is None else {_member_path(m) for m in members}
    if names is None and dest.is_file():
        return dest
    if names is not None and per_name_rel in names:
        return dest

    flat = meta / "manifest.yaml"
    shipped_flat = names is None or _FLAT_MANIFEST in names
    if shipped_flat and flat.is_file() and _flat_manifest_owner(flat) == entry.name:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(flat, dest)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w") as f:
        yaml.safe_dump(_synthesized_manifest(entry), f, default_flow_style=False, sort_keys=False)
    return dest


def _merge_move(src: Path, dst: Path) -> None:
    """Recursively move the contents of *src* into *dst*, merging directories.

    Colliding files are last-writer-wins (the freshly-installed bundle wins);
    directories are merged recursively so unrelated distributions sharing a
    parent (e.g. two packages both under site-packages) don't clobber each other.
    """
    import shutil

    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir() and not item.is_symlink() and target.is_dir() and not target.is_symlink():
            _merge_move(item, target)
            try:
                item.rmdir()
            except OSError:
                pass
            continue
        if target.is_dir() and not target.is_symlink():
            shutil.rmtree(target)
        elif target.exists() or target.is_symlink():
            target.unlink()
        shutil.move(str(item), str(target))


def _relocate_windows_site_packages(prefix: Path) -> None:
    """Move noarch python packages into the site dir Windows python.exe searches.

    cvcpkg's noarch (``platform: any``) python bundles are laid out with the Unix
    convention ``lib/pythonX.Y/site-packages/``, but a python.org-layout Windows
    interpreter (the ``python311`` package) only searches
    ``<prefix>/Lib/site-packages``.  Without this, nothing cvcpkg installs is
    importable on Windows — ``import numpy`` / ``packaging`` fail and setuptools
    falls back to python's bundled copy with no ``bdist_wheel``.  Merge any
    ``lib/python*/site-packages`` tree into ``Lib/site-packages``.

    A Windows-native bundle (built by a recipe's ``build.ps1``, which already
    targets ``Lib/site-packages``) has no Unix site dir, so this is a no-op for
    it — safe to run after every Windows install.
    """
    win_site = prefix / "Lib" / "site-packages"
    for unix_site in sorted(prefix.glob("lib/python*/site-packages")):
        if not unix_site.is_dir():
            continue
        win_site.mkdir(parents=True, exist_ok=True)
        _merge_move(unix_site, win_site)
        # Prune the now-empty Unix site path (…/lib/pythonX.Y[/site-packages]).
        for d in (unix_site, unix_site.parent):
            try:
                d.rmdir()
            except OSError:
                break


def install_entry(
    entry: CatalogEntry,
    prefix: Path,
    cache_dir: Path,
    *,
    verify_signatures: bool = False,
    require_signatures: bool = False,
    keys_dir: Path | None = None,
    target_platform: str | None = None,
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

    # A legacy flat manifest already in the prefix belongs to an earlier
    # bundle; save it before this extraction can overwrite it.
    _pin_flat_manifest(prefix)
    members = extract_bundle(archive, prefix)
    settle_bundle_manifest(entry, members, prefix)
    # On Windows, relocate any noarch python package from the Unix site path to
    # the Lib\site-packages the python.org-layout interpreter actually searches.
    if target_platform == "windows":
        _relocate_windows_site_packages(prefix)
    return archive


def _find_recipe_dir(name: str, recipes_dirs: list[Path] | None) -> Path | None:
    """The first ``<dir>/<name>`` holding a recipe.yaml; *recipes_dirs* empty
    means the default recipes directory."""
    from cvcpkg.builder import RecipeError, find_recipes_dir

    search_dirs = recipes_dirs or []
    if not search_dirs:
        try:
            search_dirs = [find_recipes_dir()]
        except RecipeError:
            return None
    for rdir in search_dirs:
        candidate = rdir / name
        if (candidate / "recipe.yaml").is_file():
            return candidate
    return None


def not_redistributable_deps(
    name: str, recipes_dirs: list[Path] | None, platform: str
) -> list[str] | None:
    """The runtime deps of *name* if its recipe is ``redistributable: false``.

    ``None`` otherwise (no such recipe, or an ordinary one).  Such a component
    is never in the catalog, so building it from its recipe is the only way to
    install it -- ``cvcpkg install`` does that whether it was requested or is
    a dependency of a catalog bundle, and whether or not
    ``--fallback-to-source`` was passed.  The deps are what that local build
    needs in the prefix first (the python interpreter, for a wheel); this is
    the resolver's ``local_only`` hook.
    """
    import yaml

    from cvcpkg.builder import Recipe, RecipeError, runtime_dependencies

    recipe_dir = _find_recipe_dir(name, recipes_dirs)
    if recipe_dir is None:
        return None
    try:
        recipe = Recipe.load(recipe_dir)
    except (RecipeError, OSError, ValueError, yaml.YAMLError):
        return None
    if recipe.redistributable:
        return None
    return [d["name"] for d in runtime_dependencies(recipe, platform)]


def source_build_deps(
    name: str, recipes_dirs: list[Path] | None, platform: str
) -> list[str] | None:
    """What a ``--fallback-to-source`` build of *name* needs installed first.

    ``None`` when there is no recipe for *name* or it does not build for
    *platform*. Otherwise the recipe's ``depends.build`` followed by its
    runtime deps, filtered to *platform*. The build runs against the
    install prefix (single-prefix model), so all of them must be resolved
    and in place before it. This is the resolver's ``local_only`` hook for
    a component with no bundle for the platform: before it existed, such a
    component was built with whatever the rest of the request happened to
    pull in, and a recipe whose build deps were not also deps of something
    else failed (cython-cp312 under ``install-deps numpy-cp312``: setuptools
    and wheel were in nobody else's closure, so its build died with "Cannot
    import 'setuptools.build_meta'").
    """
    import yaml

    from cvcpkg.builder import Recipe, RecipeError, runtime_dependencies

    recipe_dir = _find_recipe_dir(name, recipes_dirs)
    if recipe_dir is None:
        return None
    try:
        recipe = Recipe.load(recipe_dir)
    except (RecipeError, OSError, ValueError, yaml.YAMLError):
        return None
    # A recipe with no build for this platform cannot be the fallback: let the
    # resolver report the name unsatisfiable instead of starting a doomed build.
    if not any(m.platform in (platform, "any") for m in recipe.build_matrix):
        return None

    names: list[str] = []
    for d in (recipe.raw.get("depends") or {}).get("build") or []:
        if isinstance(d, str):
            dep = d.split("/", 1)[-1]
        else:
            plats = d.get("platforms")
            if plats and platform not in plats:
                continue
            dep = d["name"]
        if dep != name and dep not in names:
            names.append(dep)
    for d in runtime_dependencies(recipe, platform):
        if d["name"] != name and d["name"] not in names:
            names.append(d["name"])
    return names


def license_gated_components(
    names: list[str], recipes_dirs: list[Path] | None
) -> list[tuple[str, LicenseAcceptance]]:
    """Those of *names* whose recipe declares ``license_acceptance:``, each with
    its gate, in the order given."""
    import yaml

    from cvcpkg.builder import Recipe, RecipeError

    out: list[tuple[str, LicenseAcceptance]] = []
    for name in names:
        recipe_dir = _find_recipe_dir(name, recipes_dirs)
        if recipe_dir is None:
            continue
        try:
            gate = Recipe.load(recipe_dir).license_acceptance
        except (RecipeError, OSError, ValueError, yaml.YAMLError):
            continue
        if gate is not None:
            out.append((name, gate))
    return out


_ACCEPT_VALUES = frozenset({"1", "y", "yes", "true"})


def license_prompt(name: str, gate: LicenseAcceptance) -> str:
    """The question ``cvcpkg install`` asks on a terminal."""
    what = gate.title or "the vendor's license"
    urls = "".join(f"\n  {u}" for u in gate.urls)
    return (
        f"{name} is downloaded from its vendor under {what}:{urls}\n"
        f"Have you read these terms, and do you accept them?"
    )


def require_license_acceptance(
    name: str,
    gate: LicenseAcceptance,
    *,
    accepted: Collection[str] = (),
    environ: MutableMapping[str, str] | None = None,
    interactive: bool = False,
    confirm: Callable[[str], bool] | None = None,
) -> str:
    """Make sure the user accepted *gate* before *name* is built, and record it.

    Accepted when ``gate.env`` is already set (``1``/``yes``/``true``), when
    *name* is in *accepted* (``--accept-license``), or -- only when
    *interactive* -- when *confirm* says yes to :func:`license_prompt`.
    cvcpkg never accepts on its own: anything else raises InstallError.  On
    acceptance ``gate.env=1`` is set in *environ* (default ``os.environ``) so
    the build script, which checks it as well, sees the same answer.

    Returns how it was accepted: ``"env"``, ``"flag"`` or ``"prompt"``.
    """
    env = os.environ if environ is None else environ
    if env.get(gate.env, "").strip().lower() in _ACCEPT_VALUES:
        how = "env"
    elif name in accepted:
        how = "flag"
    elif interactive and confirm is not None:
        if not confirm(license_prompt(name, gate)):
            raise InstallError(f"{name}: license not accepted, so it was not installed.")
        how = "prompt"
    else:
        what = gate.title or "its vendor's license"
        urls = "".join(f"\n  {u}" for u in gate.urls)
        raise InstallError(
            f"{name} is downloaded from its vendor under {what}:{urls}\n"
            f"cvcpkg does not accept a license for you. Once you have read and "
            f"accept the terms, pass --accept-license {name} or set {gate.env}=1."
        )
    env[gate.env] = "1"
    return how


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

    if not recipes_dirs:
        try:
            find_recipes_dir()
        except RecipeError:
            raise InstallError(
                f"no prebuilt binary and no recipes directory found for {name}"
            ) from None
    recipe_dir = _find_recipe_dir(name, recipes_dirs)
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
    from cvcpkg.platform import _ARCHIVE_EXT, default_archive_format

    ext = _ARCHIVE_EXT[default_archive_format(entry.platform)]
    return "-".join(parts) + ext
