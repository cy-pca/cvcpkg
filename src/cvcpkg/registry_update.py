# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Replace the single-file cvcpkg binary with a newer build from a registry.

A builder that runs the PyInstaller binary (``packaging/cvcpkg.spec``; the
registry's own ``cvcpkg`` package, recipes/cvcpkg) cannot update the way a pip
install does -- there is no checkout and no pip, ``sys.executable`` *is*
cvcpkg.  It updates from the registry it builds for instead: the server it is
connected to serves a ``cvcpkg`` package per platform, and the newest one whose
upstream version is newer than the running cvcpkg replaces the binary on disk.
That is the same package ``/install.sh`` and ``/install.ps1`` install, so every
platform -- Linux, macOS, Windows and the BSDs -- updates the same way.

This module holds the steps that do not depend on the builder's state, each a
plain function so it can be tested alone; ``cli/_builder.py`` sequences them
(lock, drain, swap, restart).  Nothing here touches the binary that is running
until :func:`swap_in`, and that is one rename on POSIX (the running process
keeps the old inode) and two on Windows (a running ``.exe`` can be renamed,
never overwritten).
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from cvcpkg.semver import version_sort_key

PACKAGE = "cvcpkg"

# A Windows binary that was swapped out while running is renamed to
# ``<name>.old-<pid>`` (it cannot be deleted while it runs); a later start
# removes it (see remove_stale_binaries).
_OLD_SUFFIX = ".old-"
# The download is streamed into the binary's own directory (so the final rename
# never crosses a filesystem), under this prefix.
_PARTIAL_PREFIX = ".cvcpkg-update-"
# Well past any one download (bounded by the builder's download timeout).
_PARTIAL_MAX_AGE = 3600.0
_PROBE_TIMEOUT = 60.0
_VERSION_RE = re.compile(r"version\s+(\S+)\s*$")


class RegistryUpdateError(Exception):
    """A step of the update failed; the running binary is untouched."""


@dataclass(frozen=True)
class Candidate:
    """One registry ``cvcpkg`` package to update to."""

    version: str  # as the registry spells it: "2.5.1+cvc.1"
    archive_url: str  # absolute
    sha256: str
    platform: str
    arch: str

    @property
    def upstream(self) -> str:
        """The cvcpkg release it packages: the version without ``+cvc.N``."""
        return upstream_version(self.version)


def upstream_version(version: str) -> str:
    """*version* without its local part: ``2.5.1+cvc.2`` -> ``2.5.1``.

    The ``+cvc.N`` is the recipe's revision, not cvcpkg's: the binary reports
    ``cvcpkg, version 2.5.1`` for every revision.  Comparing the full spelling
    would make ``2.5.0+cvc.1`` newer than a running ``2.5.0`` -- PEP 440 sorts
    a local version above its public one -- and the builder would replace
    itself with the same release on every ``builder.update``.
    """
    return version.split("+", 1)[0].strip()


def binary_member(platform: str) -> str:
    """The path of the executable inside a ``cvcpkg`` package (recipes/cvcpkg)."""
    return "bin/cvcpkg.exe" if platform == "windows" else "bin/cvcpkg"


def pick_candidate(
    packages: Iterable[Mapping],
    *,
    platform: str,
    arch: str,
    running: str,
    is_newer: Callable[[str, str], bool],
    base_url: str = "",
) -> Candidate | None:
    """The newest usable package for *platform*/*arch* newer than *running*.

    *packages* are ``/v1/packages/cvcpkg`` rows.  Yanked rows, rows with no
    archive or no sha256 (nothing to verify the download against), and rows
    for another platform or arch are never picked.  *is_newer(a, b)* compares
    two cvcpkg versions (the builder's PEP 440 comparison); it sees upstream
    versions only (see upstream_version).  A relative ``archive_url`` is made
    absolute against *base_url*.
    """
    usable = [
        p
        for p in packages
        if p.get("platform") == platform
        and p.get("arch") == arch
        and not p.get("yanked")
        and p.get("archive_url")
        and p.get("sha256")
        and p.get("version")
    ]
    usable.sort(key=lambda p: version_sort_key(str(p["version"])), reverse=True)
    for p in usable:
        version = str(p["version"])
        if not is_newer(upstream_version(version), running):
            # Newest first: nothing further down is newer either.
            return None
        url = str(p["archive_url"])
        if url.startswith("/"):
            url = base_url.rstrip("/") + url
        return Candidate(
            version=version,
            archive_url=url,
            sha256=str(p["sha256"]).lower(),
            platform=platform,
            arch=arch,
        )
    return None


def download_verified(
    client,
    candidate: Candidate,
    dest_dir: Path,
    *,
    headers: Mapping[str, str] | None = None,
) -> Path:
    """Stream *candidate*'s archive into *dest_dir*; return it once its sha256 matches.

    The file is removed again on any failure.  *client* is an ``httpx.Client``.
    """
    fd, name = tempfile.mkstemp(prefix=_PARTIAL_PREFIX, suffix=".archive", dir=dest_dir)
    path = Path(name)
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as out:
            with client.stream("GET", candidate.archive_url, headers=dict(headers or {})) as resp:
                if resp.status_code >= 400:
                    raise RegistryUpdateError(
                        f"download of {candidate.version} failed: HTTP {resp.status_code}"
                    )
                for chunk in resp.iter_bytes():
                    digest.update(chunk)
                    out.write(chunk)
        got = digest.hexdigest()
        if got != candidate.sha256:
            raise RegistryUpdateError(
                f"{candidate.version}: sha256 mismatch (registry says {candidate.sha256}, "
                f"downloaded {got})"
            )
        return path
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def extract_binary(archive: Path, member: str, dest: Path) -> None:
    """Write *archive*'s *member* (``bin/cvcpkg``) to *dest*, executable.

    Only that one regular file is read; nothing else in the archive is
    extracted anywhere.  Builders upload gzip tars (zip on Windows), whatever
    the ``archive_url`` suffix says, so the format is sniffed.
    """
    from cvcpkg._archive import archive_format, open_tar

    fmt = archive_format(archive)
    wanted = {member, f"./{member}"}
    if fmt == "zip":
        with zipfile.ZipFile(archive) as zf:
            names = [n for n in zf.namelist() if n.replace("\\", "/") in wanted]
            if not names:
                raise RegistryUpdateError(f"{archive.name} has no {member}")
            with zf.open(names[0]) as src, open(dest, "wb") as out:
                shutil.copyfileobj(src, out)
    else:
        try:
            with open_tar(archive, fmt=fmt) as tf:
                info = next((m for m in tf.getmembers() if m.name in wanted), None)
                if info is None:
                    raise RegistryUpdateError(f"{archive.name} has no {member}")
                if not info.isfile():
                    raise RegistryUpdateError(f"{member} in {archive.name} is not a regular file")
                src = tf.extractfile(info)
                if src is None:
                    raise RegistryUpdateError(f"cannot read {member} from {archive.name}")
                with src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)
        except RegistryUpdateError:
            raise
        except Exception as exc:  # noqa: BLE001 - a corrupt or unreadable archive
            raise RegistryUpdateError(f"cannot read {archive.name}: {exc}") from exc
    os.chmod(dest, 0o755)


def probe_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment to start a *different* cvcpkg binary in.

    The PyInstaller bootloader of this process left ``_PYI_*`` / ``_MEIPASS2``
    in the environment; a binary started with them believes it is this
    process's child and runs this process's unpacked code instead of its own.
    """
    from cvcpkg.selfexec import cvcpkg_env

    env = cvcpkg_env(base)
    for key in list(env):
        if key.startswith("_PYI_") or key == "_MEIPASS2":
            del env[key]
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env["CVCPKG_ENTRY"] = "client"
    return env


def probe_version(binary: Path, *, timeout: float = _PROBE_TIMEOUT) -> str | None:
    """The version ``<binary> --version`` reports, or None if it does not run."""
    try:
        r = subprocess.run(  # noqa: S603 - our own binary
            [str(binary), "--version"],
            env=probe_env(),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            cwd=os.path.abspath(os.sep),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    lines = [line for line in r.stdout.strip().splitlines() if line.strip()]
    if not lines:
        return None
    m = _VERSION_RE.search(lines[-1])
    return m.group(1) if m else None


def swap_in(new: Path, target: Path) -> Path | None:
    """Make *new* the binary at *target*; return where the old one went, if anywhere.

    POSIX: one atomic rename over *target* -- the running process keeps
    executing the old inode, and nothing ever sees a half-written file.
    Windows cannot replace a running ``.exe`` but can rename it, so the old one
    moves aside to ``<name>.old-<pid>`` (returned; removed by a later start)
    and *new* takes its name.  Should that second rename fail, the old binary
    is moved back.
    """
    if sys.platform != "win32":
        os.replace(new, target)
        return None
    old = target.with_name(f"{target.name}{_OLD_SUFFIX}{os.getpid()}")
    os.replace(target, old)
    try:
        os.replace(new, target)
    except OSError:
        os.replace(old, target)
        raise
    return old


def remove_stale_binaries(target: Path, *, now: float | None = None) -> None:
    """Delete binaries earlier Windows updates moved aside, and abandoned downloads.

    Best effort: a moved-aside binary that still runs (another worker started
    from it) cannot be deleted yet and is left for the next start.  A partial
    download is removed only once it is _PARTIAL_MAX_AGE old: a builder
    sharing the binary (a fleet's other worker) may be writing it right now.
    """
    import time

    now = time.time() if now is None else now
    try:
        siblings = list(target.parent.iterdir())
    except OSError:
        return
    for p in siblings:
        if p.name.startswith(f"{target.name}{_OLD_SUFFIX}"):
            pass
        elif p.name.startswith(_PARTIAL_PREFIX):
            try:
                if now - p.stat().st_mtime < _PARTIAL_MAX_AGE:
                    continue
            except OSError:
                continue
        else:
            continue
        try:
            p.unlink()
        except OSError:
            pass


def stage_binary(
    client,
    candidate: Candidate,
    target: Path,
    *,
    headers: Mapping[str, str] | None = None,
) -> Path:
    """Download, verify, extract and test *candidate*; return the staged binary.

    The staged binary sits next to *target* (same filesystem, so swap_in is a
    rename) and has already answered ``--version`` with *candidate*'s upstream
    version.  On any failure nothing is left behind and RegistryUpdateError
    (or the transport's own error) is raised; *target* is never touched here.
    """
    dest_dir = target.parent
    if not os.access(dest_dir, os.W_OK):
        raise RegistryUpdateError(f"cannot write to {dest_dir}, where the binary lives")
    archive = download_verified(client, candidate, dest_dir, headers=headers)
    try:
        fd, name = tempfile.mkstemp(prefix=_PARTIAL_PREFIX, suffix=target.suffix, dir=dest_dir)
        os.close(fd)
        staged = Path(name)
        try:
            extract_binary(archive, binary_member(candidate.platform), staged)
            reported = probe_version(staged)
            if reported is None:
                raise RegistryUpdateError(
                    f"the downloaded {candidate.version} binary does not run "
                    f"(`{staged.name} --version` failed)"
                )
            if upstream_version(reported) != candidate.upstream:
                raise RegistryUpdateError(
                    f"the downloaded {candidate.version} binary reports version "
                    f"{reported}, not {candidate.upstream}"
                )
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
        return staged
    finally:
        archive.unlink(missing_ok=True)
