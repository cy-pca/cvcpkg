# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Liveness heartbeat for build scratch trees, so reapers can spare them.

Builders share ``/tmp`` with a disk reaper (``scripts/reap-stale-build-dirs.sh``,
run from the deploy workflow) whose whole job is to delete leaked build trees
before a host fills its disk.  Telling a *leaked* tree from an *in-flight* one
from the outside is the hard part, and the obvious test is wrong:

    find /tmp -maxdepth 1 -name 'cvcpkg-*' -type d -mmin +60 -exec rm -rf {} +

``-maxdepth 1 -type d -mmin`` tests the **top directory's own mtime**, and a
directory's mtime only advances when an entry is added or removed *directly*
inside it.  A build tree creates ``build/`` and ``install/`` in its first
second and then writes tens of thousands of files several levels down, so the
top directory's mtime freezes almost immediately and the tree looks stale after
an hour no matter how hard it is working.  On 2026-08-04 that deleted
``/tmp/cvcpkg-haiku-image-b6wd0qrn`` 90 minutes into a build, orphaning six
processes onto a deleted image file.  Raising the threshold only moves the
cliff: ``llvm18`` declares ``timeout_seconds: 18000`` (5 hours).

The fix is for the owner of the tree to publish liveness, and the channel has
to survive the constraint that **the reaper may be a different user**.  Scratch
roots come from :func:`tempfile.mkdtemp`, which creates them ``0700``, so a
non-root reaper cannot open — cannot even ``stat`` — anything *inside* the
tree.  It can always ``stat`` the root itself, because ``/tmp`` is ``1777``.

So the load-bearing signal is deliberately the crudest one available:

1. **The root directory's own mtime**, refreshed by :func:`os.utime` on every
   beat.  Needs no new protocol, no read permission, no parsing; it makes the
   *existing* ``-mmin`` predicate correct instead of replacing it.  This is the
   signal the reaper relies on.
2. **A ``.cvcpkg-heartbeat`` file** in the root recording pid / comm / host /
   boot id / label / start time.  Readable only by the owner or root — which
   covers every reaper we actually run — and it is what turns "something was
   deleted" into "``haiku-image``, pid 21114, started 09:15, still running".
   It also lets a reaper spare a tree whose heartbeat *thread* wedged but whose
   process is demonstrably alive, and (the other direction) reclaim a tree
   immediately once the recorded pid is gone.

One background thread serves every watched root, not one per build: the
builder daemon runs thousands of jobs in a single long-lived process, and a
thread per job would be an unbounded leak.  Roots that have been deleted drop
themselves from the watch set, so a caller that never unwatches leaks nothing.

Everything here is best-effort: a heartbeat failure must never fail a build, so
every operation swallows its errors.  A tree with no heartbeat at all (an older
cvcpkg, a foreign ``cvcpkg-*`` directory) still gets reaped by the script's
fallback, which walks for the newest mtime anywhere in the tree rather than
trusting the root.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

__all__ = [
    "HEARTBEAT_NAME",
    "beat_once",
    "boot_id",
    "read_heartbeat",
    "unwatch",
    "watch",
    "watched",
]

#: Name of the heartbeat file, relative to the scratch root.  The reaper script
#: hard-codes the same literal; keep the two in sync.
HEARTBEAT_NAME = ".cvcpkg-heartbeat"

#: Default seconds between beats.  Well under the reaper's smallest sensible
#: idle threshold (60 minutes) so beats can be missed repeatedly — a paused VM,
#: a stopped process, a loaded host — without the tree looking abandoned.
#: ``CVCPKG_HEARTBEAT_INTERVAL`` overrides it, chiefly so tests need not sleep.
DEFAULT_INTERVAL = 60.0


def _interval() -> float:
    try:
        return max(0.05, float(os.environ.get("CVCPKG_HEARTBEAT_INTERVAL", "")))
    except ValueError:
        return DEFAULT_INTERVAL


def boot_id() -> str:
    """A value that changes when the host reboots, or ``""`` if unavailable.

    Recorded alongside the pid so a reaper never trusts a pid across a reboot,
    where it certainly refers to a different process.  Linux exposes one; the
    BSDs do not, and there the check simply degrades to "skip".
    """
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _comm(pid: int) -> str:
    """The process's command name, or ``""``.

    Paired with the pid to blunt pid reuse: a recycled pid almost never lands
    on a process with the same ``comm``.
    """
    try:
        return Path(f"/proc/{pid}/comm").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def read_heartbeat(root: Path | str) -> dict[str, str]:
    """Parse ``<root>/.cvcpkg-heartbeat`` into a dict; ``{}`` if unreadable.

    The format is deliberately ``key=value`` lines: the reaper is POSIX ``sh``
    on hosts as varied as Ubuntu, FreeBSD and NetBSD, and ``sed`` can read this
    without a JSON parser.
    """
    path = Path(root) / HEARTBEAT_NAME
    out: dict[str, str] = {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


def beat_once(root: Path | str, *, label: str = "", started: int | None = None) -> bool:
    """Publish liveness for *root* once.  Returns False once *root* is gone.

    Never raises: a full disk or a racing ``rm -rf`` must not turn into a build
    failure.  A ``False`` return is how the watcher thread learns to forget a
    root, so it is reserved for "the directory no longer exists", not for a
    transient write error.
    """
    root = Path(root)
    # utime() on the root is the signal a cross-user reaper can actually see:
    # mkdtemp roots are 0700, so nothing *inside* is stat-able by another
    # unprivileged user, but /tmp is 1777 so the root itself always is.
    try:
        os.utime(root, None)
    except FileNotFoundError:
        return False
    except OSError:
        pass

    pid = os.getpid()
    body = "\n".join(
        (
            f"pid={pid}",
            f"comm={_comm(pid)}",
            f"host={socket.gethostname()}",
            f"boot={boot_id()}",
            f"label={label}",
            f"started={int(time.time()) if started is None else started}",
            f"beat={int(time.time())}",
            "",
        )
    )
    # Write-then-rename so a reaper never reads a half-written file and a crash
    # mid-beat leaves the previous (still valid) heartbeat intact.
    tmp = root / f"{HEARTBEAT_NAME}.tmp"
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, root / HEARTBEAT_NAME)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
    return True


# ── shared watcher ──────────────────────────────────────────────

_lock = threading.Lock()
_watched: dict[str, tuple[str, int]] = {}  # resolved path -> (label, started)
_thread: threading.Thread | None = None
_wake = threading.Event()


def _run() -> None:
    global _thread
    while True:
        with _lock:
            items = list(_watched.items())
            if not items:
                # Nothing left to watch: retire rather than spin forever.  A
                # later watch() starts a fresh thread.
                _thread = None
                return
        for path, (label, started) in items:
            with _lock:
                if path not in _watched:
                    # Unwatched while this iteration was in flight; its final
                    # beat has already run and a periodic one after it would
                    # contradict "unwatch stops the beats".
                    continue
            if not beat_once(path, label=label, started=started):
                with _lock:
                    _watched.pop(path, None)
        _wake.wait(_interval())
        _wake.clear()


def watch(root: Path | str, *, label: str = "") -> None:
    """Start publishing liveness for *root* until :func:`unwatch` or deletion."""
    key = str(Path(root))
    started = int(time.time())
    beat_once(key, label=label, started=started)  # publish before returning
    global _thread
    with _lock:
        if key in _watched:
            return
        _watched[key] = (label, started)
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_run, name="cvcpkg-heartbeat", daemon=True)
            _thread.start()
    # Wake the shared thread so it adopts the new root NOW.  Without this a
    # thread already parked in its interval wait -- up to 60 s, e.g. left
    # over from an earlier build_all whose watch deliberately outlives a
    # raise -- would not beat the new root (nor re-read a changed
    # CVCPKG_HEARTBEAT_INTERVAL) until that wait expired.  Seen as a real
    # failure: the macos-latest suite runs ~2900 tests in one process, an
    # earlier test left such a parked thread, and a freshly watched tree got
    # no beat for the whole 10 s assertion window.
    _wake.set()


def unwatch(root: Path | str) -> None:
    """Stop publishing liveness for *root*, beating one final time.

    The final beat matters: if the process dies before its own cleanup runs,
    the reaper should measure the tree's age from the moment work stopped, not
    from whenever the last periodic beat happened to land.
    """
    key = str(Path(root))
    with _lock:
        existed = _watched.pop(key, None) is not None
    if existed:
        beat_once(key)
    _wake.set()


@contextmanager
def watched(root: Path | str, *, label: str = "") -> Iterator[Path]:
    """Context manager form of :func:`watch` / :func:`unwatch`."""
    watch(root, label=label)
    try:
        yield Path(root)
    finally:
        unwatch(root)
