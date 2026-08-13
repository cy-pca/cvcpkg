# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Builder disk reclamation — orphaned job scratch dirs and the download cache.

A build job runs inside a per-job root (``cvcpkg-job-<recipe>-<rand>/``) in the
builder's work dir, and ``_execute_job`` removes that tree in a ``finally``.
That covers every path the job *returns* through — but not the one that
actually leaks: when the builder process is **killed mid-job** (a deploy
restart, SIGKILL from the recovery runbook, OOM, host reboot) the ``finally``
never runs and the tree is stranded. The leak is therefore proportional to how
often builders are restarted, which is "every deploy", and it accumulates
silently until a build dies with ENOSPC (dev cluster, 2026-08-02: 28 GiB of
stranded job dirs, 96% full, three CUDA jobs failed).

Two sweeps, deliberately different:

* **Startup** (``max_age_seconds=0``) — every job dir present when a builder
  starts is by definition an orphan: the single-instance pidfile guard means no
  other builder shares this work dir, and this process owns nothing yet. So the
  startup sweep needs no age heuristic and reclaims everything immediately.
* **Periodic** — a long-lived builder that has not restarted still wants a
  safety net (a wedged job thread that never reached its ``finally``). This one
  IS age-gated and takes the in-flight job roots as *keep*, so it can never
  delete a live build of its own.

"Age" here is deliberately not the top directory's mtime, which is the trap the
``/tmp`` reaper fell into (see :mod:`cvcpkg.heartbeat`): a directory's mtime
only advances when an entry is added or removed *directly* inside it, so a
build tree looks stale within seconds of creating ``build/`` and ``install/``.
:func:`_is_active` asks the heartbeat, the owning pid and the newest mtime
*anywhere* in the tree instead — the same ladder, in the same order, as
``scripts/reap-stale-build-dirs.sh``, so an in-process sweep and an
out-of-process one can never disagree about what is live.

The download cache (``~/.cache/cvcpkg/<sha256>/``) is content-addressed, so
pruning it is always safe — the only cost of over-pruning is re-downloading.
It is swept by age, and defaults are conservative for that reason.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Per-job roots, named unambiguously.  A ``cvcpkg-job-*`` tree is always scratch
# and is removed whole (its own prefix/out children live inside it).
_JOB_ROOT_PREFIXES = ("cvcpkg-job-", "cvcpkg-prefix-", "cvcpkg-out-")

# The pre-job-root layout put build dirs straight in the work dir as
# ``cvcpkg-<recipe>-<rand>`` (still present on builders that have been leaking
# since before job roots existed).  This pattern is DANGEROUSLY broad: the fleet
# supervisor names each per-server work dir after the server host, so the
# container for cvcpkg.org is literally ``cvcpkg-org`` and matches it.  Treating
# that as scratch would delete the whole container — including a live job root
# inside it.  So a bare match only counts when the directory holds no scratch
# children of its own (see _candidates).
_LEGACY_SCRATCH_PREFIX = "cvcpkg-"

# Never delete these, whatever the pattern says: work-dir roots are themselves
# conventionally named ``cvcpkg-builder`` (``--work-dir /tmp/cvcpkg-builder``),
# so a naive ``cvcpkg-*`` glob in /tmp eats the directory it is meant to clean.
_NEVER_DELETE = {"cvcpkg-builder", "cvcpkg-recipe-cache"}

# How long past a heartbeat's last beat its recorded pid is still believed.
# Matches the reaper script's -P default (1440 minutes).
_PID_TRUST_SECONDS = 24 * 60 * 60


@dataclass
class GcResult:
    """What a sweep reclaimed."""

    removed: int = 0
    freed_bytes: int = 0
    paths: list[Path] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.removed > 0

    @property
    def freed_mib(self) -> float:
        return self.freed_bytes / (1024 * 1024)

    def merge(self, other: GcResult) -> GcResult:
        self.removed += other.removed
        self.freed_bytes += other.freed_bytes
        self.paths.extend(other.paths)
        return self


def _dir_size(path: Path) -> int:
    """Best-effort recursive size in bytes.  Never raises."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path, onerror=lambda _e: None):
            for name in files:
                try:
                    st = os.lstat(os.path.join(root, name))
                except OSError:
                    continue
                total += st.st_size
    except Exception:
        pass
    return total


def _newest_mtime(path: Path, *, floor: float = 0.0) -> float:
    """Newest mtime anywhere in *path*, not just the top directory's own.

    The top directory's mtime is the wrong clock, and the reason a sweep could
    delete a live build: a directory's mtime only advances when an entry is
    added or removed *directly* inside it, so a tree that creates ``build/``
    and ``install/`` up front and then writes tens of thousands of files
    several levels down looks untouched from the moment it starts working.
    See :mod:`cvcpkg.heartbeat` for the full account.

    Returns early once something newer than *floor* turns up: for an active
    tree -- the case where being wrong is expensive -- that is a handful of
    stats rather than a full walk of a multi-gigabyte build directory.
    """
    try:
        newest = path.stat().st_mtime
    except OSError:
        return 0.0
    if newest > floor:
        return newest
    for root, dirs, files in os.walk(path, onerror=lambda _e: None):
        for name in dirs + files:
            try:
                mtime = os.lstat(os.path.join(root, name)).st_mtime
            except OSError:
                continue
            if mtime > newest:
                newest = mtime
                if newest > floor:
                    return newest
    return newest


def _is_active(path: Path, cutoff: float) -> bool:
    """True when *path* shows any sign of life at or after *cutoff*.

    Same ladder as ``scripts/reap-stale-build-dirs.sh``, cheapest first, so the
    two reapers cannot disagree about what counts as a live build:

    1. the root's own mtime, which :mod:`cvcpkg.heartbeat` keeps fresh;
    2. an explicit ``.cvcpkg-heartbeat`` naming a process that is still alive
       here -- covers a build whose heartbeat thread wedged but whose work
       carried on;
    3. anything anywhere in the tree modified since *cutoff*, for trees written
       by an older cvcpkg that publishes no heartbeat at all.
    """
    from cvcpkg.heartbeat import HEARTBEAT_NAME, boot_id, read_heartbeat

    try:
        if path.stat().st_mtime >= cutoff:
            return True
    except OSError:
        return False

    hb_path = path / HEARTBEAT_NAME
    try:
        hb_mtime = hb_path.stat().st_mtime
    except OSError:
        hb_mtime = 0.0

    # A pid is only believed for so long after its heartbeat went quiet.
    # Builders are long-lived and every cvcpkg process is `python3`, so given
    # enough weeks the pid space wraps and a stranded heartbeat starts pointing
    # at an unrelated live python — which would make its tree immortal and
    # refill the disk these sweeps exist to protect.
    if hb_mtime >= cutoff - _PID_TRUST_SECONDS:
        hb = read_heartbeat(path)
        pid = hb.get("pid", "")
        if pid.isdigit():
            # A pid means nothing on another host or across a reboot.
            host_ok = not hb.get("host") or hb["host"] == socket.gethostname()
            boot_now = boot_id()
            boot_ok = not hb.get("boot") or not boot_now or hb["boot"] == boot_now
            if host_ok and boot_ok:
                try:
                    os.kill(int(pid), 0)
                    return True
                except PermissionError:
                    return True  # exists, just not ours
                except OSError:
                    pass

    return _newest_mtime(path, floor=cutoff) >= cutoff


def _is_job_root(name: str) -> bool:
    if name in _NEVER_DELETE:
        return False
    return any(name.startswith(p) for p in _JOB_ROOT_PREFIXES)


def _scratch_children(path: Path) -> list[Path]:
    """Job roots directly inside *path* (empty when it is not a container)."""
    out: list[Path] = []
    try:
        for sub in path.iterdir():
            try:
                if sub.is_dir() and not sub.is_symlink() and _is_job_root(sub.name):
                    out.append(sub)
            except OSError:
                continue
    except OSError:
        return []
    return out


def _candidates(work_dir: Path) -> list[Path]:
    """Scratch dirs directly in *work_dir*, plus one level down.

    The fleet supervisor gives each served server its own subdirectory
    (``work_dir/<server-slug>/cvcpkg-job-*``), while a single-server builder
    puts job roots straight in the work dir — one sweep covers either layout.

    Order matters: an unambiguous job root is scratch even though it *contains*
    scratch children (its own prefix/out dirs), whereas a directory that merely
    holds job roots is a container and must survive — the fleet slug for
    cvcpkg.org is ``cvcpkg-org``, which the legacy bare pattern would otherwise
    match and delete out from under a running build.
    """
    found: list[Path] = []
    try:
        entries = list(work_dir.iterdir())
    except OSError:
        return found
    for entry in entries:
        try:
            if not entry.is_dir() or entry.is_symlink():
                continue
        except OSError:
            continue
        if _is_job_root(entry.name):
            found.append(entry)
            continue
        children = _scratch_children(entry)
        if children:
            found.extend(children)  # container: sweep its jobs, keep the dir
            continue
        if entry.name not in _NEVER_DELETE and entry.name.startswith(_LEGACY_SCRATCH_PREFIX):
            found.append(entry)
    return found


#: Last per-entry errors from the most recent :func:`_rmtree` that left the
#: tree in place, for diagnostics (the gc tests surface it on failure).
_LAST_RMTREE_ERRORS: list[str] = []


def _rmtree(path: Path) -> None:
    """``shutil.rmtree`` with retries, collecting errors instead of hiding them.

    Windows needs both.  Measured on the windows-latest runner (forensics in
    test_a_live_pid_stops_protecting_once_its_heartbeat_is_ancient): the sweep
    correctly decides to delete, yet the FULL tree remains after ~6 s of
    suppressed retries.  The shape matches NTFS delete-pending semantics: a
    scanner holding share-delete handles lets each unlink "succeed" into a
    tombstone that still lists in the directory, the parent rmdir then fails
    DIR_NOT_EMPTY, and repeated attempts can retrigger scans and renew the
    handles — so backoff alone cannot be relied on.  The collected errors
    exist to confirm or refute exactly that on the next failure.  POSIX
    succeeds on the first attempt and never sleeps.
    """
    _LAST_RMTREE_ERRORS.clear()

    def _note(func: Any, p: Any, exc: Any) -> None:
        if isinstance(exc, tuple):  # onerror-style exc_info triple
            exc = exc[1]
        if len(_LAST_RMTREE_ERRORS) < 8:
            _LAST_RMTREE_ERRORS.append(f"{getattr(func, '__name__', '?')}({p}): {exc!r}")

    kwargs: dict[str, Any] = {"onexc": _note} if sys.version_info >= (3, 12) else {"onerror": _note}
    delay = 0.1
    for attempt in range(7):
        if attempt:
            time.sleep(delay)
            delay *= 2
        _LAST_RMTREE_ERRORS.clear()
        shutil.rmtree(path, **kwargs)
        if not path.exists():
            _LAST_RMTREE_ERRORS.clear()
            return


def sweep_work_dir(
    work_dir: Path | str,
    *,
    max_age_seconds: float = 0,
    keep: Iterable[Path | str] = (),
    dry_run: bool = False,
    now: float | None = None,
) -> GcResult:
    """Remove stale job scratch dirs under *work_dir*.

    ``max_age_seconds=0`` removes every scratch dir regardless of age — correct
    at builder startup, where anything present is an orphan of a previous
    incarnation.  A positive value only removes trees that have shown no sign
    of life for that long (:func:`_is_active`), for periodic use on a running
    builder; a build still working is spared however long it has been running.

    *keep* is the set of job roots currently in flight; they are never removed
    even if they look stale.  Best-effort throughout: an unreadable or
    concurrently-deleted tree is skipped rather than raising into the caller.
    """
    result = GcResult()
    work_dir = Path(work_dir)
    if not work_dir.is_dir():
        return result
    now = time.time() if now is None else now
    keep_resolved: set[str] = set()
    for k in keep:
        try:
            keep_resolved.add(str(Path(k).resolve()))
        except OSError:
            keep_resolved.add(str(k))

    for path in _candidates(work_dir):
        try:
            if str(path.resolve()) in keep_resolved:
                continue
        except OSError:
            continue
        if max_age_seconds > 0 and _is_active(path, now - max_age_seconds):
            continue
        size = _dir_size(path)
        if not dry_run:
            _rmtree(path)
            # Even with retries a partial tree can survive; only count it as
            # reclaimed when the directory is actually gone.
            if path.exists():
                continue
        result.removed += 1
        result.freed_bytes += size
        result.paths.append(path)
    return result


def sweep_cache(
    cache_dir: Path | str,
    *,
    max_age_seconds: float,
    dry_run: bool = False,
    now: float | None = None,
) -> GcResult:
    """Remove download-cache entries not touched within *max_age_seconds*.

    The cache is content-addressed (``<cache>/<sha256>/<filename>``), so an
    over-eager prune costs a re-download and nothing else.  A non-positive
    *max_age_seconds* disables the sweep entirely rather than deleting the
    whole cache — "0 means off" matches the server's retention knobs, and the
    alternative reading (delete everything) is a footgun in a periodic loop.
    """
    result = GcResult()
    cache_dir = Path(cache_dir)
    if max_age_seconds <= 0 or not cache_dir.is_dir():
        return result
    now = time.time() if now is None else now
    try:
        entries = list(cache_dir.iterdir())
    except OSError:
        return result
    for entry in entries:
        try:
            if not entry.is_dir() or entry.is_symlink():
                continue
            # Use the newest mtime in the entry so a cache hit that rewrites
            # nothing still looks recent if the file was replaced.
            if now - entry.stat().st_mtime < max_age_seconds:
                continue
        except OSError:
            continue
        size = _dir_size(entry)
        if not dry_run:
            _rmtree(entry)
            if entry.exists():
                continue
        result.removed += 1
        result.freed_bytes += size
        result.paths.append(entry)
    return result
