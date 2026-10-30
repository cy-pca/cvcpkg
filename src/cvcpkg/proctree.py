# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Stop a build job's processes as a whole: everything its scripts started.

A fleet builder runs several jobs at once, each on a thread of one process, so
"this job's processes" cannot be told apart by ancestry from the builder.  So
each job gets a :class:`ProcessTree`, bound to the job's thread with
:func:`bound`, and the places that start a build's long-running processes (the
build and test scripts, the WSL and Haiku delegations) start them through
:func:`popen`, which puts them in the calling thread's tree:

- **POSIX:** the process leads a session of its own (``start_new_session``),
  so its process group holds everything it starts (make, ninja, every
  compiler they fork), and :meth:`ProcessTree.kill` signals the group:
  SIGTERM, then SIGKILL for whatever is left after a grace period.
- **Windows:** the process is put in a Job Object, which every process it
  starts joins too, including one whose parent has already exited (an
  orphaned cl.exe or mspdbsrv.exe that ``taskkill /T`` can no longer reach by
  parent), and :meth:`ProcessTree.kill` terminates the job.  ``taskkill /T``
  runs first for anything outside it: a process the assignment missed, or one
  started with CREATE_BREAKAWAY_FROM_JOB.

With no tree bound (``cvcpkg build`` on a developer's machine), :func:`popen`
is plain :class:`subprocess.Popen`.

The builder needs this because the server can stop wanting a job while it is
still building: a force-cancel, the build timeout, or the offline reaper
failing every job of a builder whose heartbeats lapsed.  A build whose result
nobody will accept went on holding the builder's slot, cores and memory.  On a
16 GB builder running two 20-core jobs that stalled its heartbeats again, so
every job dispatched to it next failed the same way (sandipaws, 2026-10-25/26).

Everything here is best effort: a kill that fails leaves the build running,
as before, and never raises.
"""

from __future__ import annotations

import functools
import os
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

# How long a killed POSIX process group gets to exit on SIGTERM before it is
# sent SIGKILL.  Long enough for compilers to remove their temp files.
KILL_GRACE_SECS = 5.0

_local = threading.local()


class ProcessTreeKilledError(RuntimeError):
    """A process was to be started in a tree that has already been killed."""


class ProcessTree:
    """The processes one build job started, to be stopped together.

    Thread-safe: the job's own thread adopts processes while another thread
    (the builder's socket loop, its reconcile) may kill the tree.  Once
    killed, the tree stays killed: a process adopted afterwards is killed on
    the spot, and :func:`popen` refuses to start one.
    """

    def __init__(self, label: str = "") -> None:
        self.label = label
        self._lock = threading.Lock()
        self._procs: list[subprocess.Popen] = []
        self._killed = False
        self._job: int | None = None  # Windows Job Object handle, made on first adopt

    @property
    def killed(self) -> bool:
        return self._killed

    def popen_kwargs(self) -> dict:
        """Extra :class:`subprocess.Popen` arguments for a process of this tree."""
        if sys.platform == "win32":
            return {}
        return {"start_new_session": True}

    def adopt(self, proc: subprocess.Popen) -> None:
        """Make *proc*, started with :meth:`popen_kwargs`, part of this tree."""
        with self._lock:
            self._procs.append(proc)
            if sys.platform == "win32":
                self._assign(proc)
            killed = self._killed
        if killed:
            # Killed while this process was being started.
            self._kill_procs([proc], grace=0.0)

    def kill(self, grace: float = KILL_GRACE_SECS) -> None:
        """Stop every running process of this tree and everything it started.

        Blocks for up to *grace* seconds on POSIX (see :data:`KILL_GRACE_SECS`).
        """
        with self._lock:
            self._killed = True
            procs = list(self._procs)
        self._kill_procs(procs, grace)

    def close(self) -> None:
        """Release the Windows job handle.  The processes are left alone."""
        with self._lock:
            job, self._job = self._job, None
            if job:
                try:
                    _kernel32().CloseHandle(job)
                except OSError:
                    pass

    # -- internals ---------------------------------------------------

    def _kill_procs(self, procs: list[subprocess.Popen], grace: float) -> None:
        # Only processes this side has not reaped: until then the pid, and so
        # the POSIX process group id, cannot have been given to another
        # process (on Windows, the Popen's open handle holds the pid).
        live = [p for p in procs if p.returncode is None]
        try:
            if sys.platform == "win32":
                self._kill_windows(live)
            else:
                _kill_groups([p.pid for p in live], grace)
        except Exception:  # noqa: BLE001 - best effort, see the module docstring
            pass

    def _assign(self, proc: subprocess.Popen) -> None:
        """Windows: put *proc* in this tree's Job Object (made on first use)."""
        handle = getattr(proc, "_handle", None)
        if handle is None:
            return
        try:
            k32 = _kernel32()
            if self._job is None:
                job = k32.CreateJobObjectW(None, None)
                if not job:
                    return
                _allow_breakaway(k32, job)
                self._job = job
            k32.AssignProcessToJobObject(self._job, int(handle))
        except OSError:
            pass  # kill() still has taskkill /T

    def _kill_windows(self, live: list[subprocess.Popen]) -> None:
        for p in live:
            # Walks the tree by parent while the root is alive, so it also
            # reaches what the job does not hold.
            try:
                subprocess.run(  # noqa: S603, S607 - fixed command, our own child's pid
                    ["taskkill", "/T", "/F", "/PID", str(p.pid)],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    timeout=60,
                    check=False,
                )
            except (OSError, subprocess.SubprocessError):
                pass
        # Under the lock, which close() takes too: a handle closed meanwhile
        # could already name another job's Job Object.
        with self._lock:
            if self._job:
                # Everything in the job, orphans included, and whatever the
                # taskkill walk raced with.
                _kernel32().TerminateJobObject(self._job, 1)


def stop(proc: subprocess.Popen, grace: float = KILL_GRACE_SECS) -> None:
    """Stop one process the caller gave up on, and everything it started.

    For a caller's own timeout, where the rest of the job goes on: haikuhost
    stops an ssh that never exits after its remote command is done.  On POSIX a
    process that leads its own process group (``start_new_session=True``,
    which :func:`popen` passes inside a tree) has the whole group signalled the
    way :meth:`ProcessTree.kill` does it: SIGTERM, then SIGKILL after *grace*.
    Any other process, and on Windows every process, gets ``Popen.kill``.
    Best effort, and never raises.
    """
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32" or os.getpgid(proc.pid) != proc.pid:
            proc.kill()
            return
        import signal

        _signal_group(proc.pid, signal.SIGTERM)
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            # Reap the leader as it goes: an unreaped zombie still answers for
            # its group, and the wait would last the whole grace period.
            if proc.poll() is not None and not _signal_group(proc.pid, 0):
                return
            time.sleep(0.05)
        _signal_group(proc.pid, signal.SIGKILL)
    except Exception:  # noqa: BLE001 - best effort, see the module docstring
        pass


def _kill_groups(pgids: list[int], grace: float) -> None:
    """POSIX: SIGTERM each process group, SIGKILL what is left after *grace*."""
    import signal

    for pgid in pgids:
        _signal_group(pgid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    alive = list(pgids)
    while alive and time.monotonic() < deadline:
        time.sleep(0.1)
        alive = [g for g in alive if _signal_group(g, 0)]
    for pgid in alive:
        _signal_group(pgid, signal.SIGKILL)


def _signal_group(pgid: int, sig: int) -> bool:
    """Send *sig* to process group *pgid*; False when the group is gone."""
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return False
    except OSError:
        pass  # EPERM: a member we may not signal; the group still exists
    return True


@functools.lru_cache(maxsize=1)
def _kernel32():
    """kernel32 with the prototypes used here, on a private WinDLL instance.

    Private so the argtypes set here never clash with another module's use of
    ``ctypes.windll.kernel32``.
    """
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    )
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k32.TerminateJobObject.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = (wintypes.HANDLE,)
    k32.CloseHandle.restype = wintypes.BOOL
    return k32


def _allow_breakaway(k32, job) -> None:
    """Let a process that asks for CREATE_BREAKAWAY_FROM_JOB leave the job.

    Without JOB_OBJECT_LIMIT_BREAKAWAY_OK such a CreateProcess fails with
    access denied, which would break a build tool that works today.  Only an
    explicit request breaks away; ``taskkill /T`` still reaches those.
    """
    import ctypes
    from ctypes import wintypes

    class _BasicLimits(ctypes.Structure):  # JOBOBJECT_BASIC_LIMIT_INFORMATION
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    limits = _BasicLimits()
    limits.LimitFlags = 0x00000800  # JOB_OBJECT_LIMIT_BREAKAWAY_OK
    k32.SetInformationJobObject(
        job, 2, ctypes.byref(limits), ctypes.sizeof(limits)
    )  # 2 = JobObjectBasicLimitInformation


@contextmanager
def bound(tree: ProcessTree | None) -> Iterator[ProcessTree | None]:
    """Make *tree* the calling thread's tree for :func:`popen` in this block."""
    previous = getattr(_local, "tree", None)
    _local.tree = tree
    try:
        yield tree
    finally:
        _local.tree = previous


def current() -> ProcessTree | None:
    """The tree bound to the calling thread, if any."""
    return getattr(_local, "tree", None)


def popen(args, **kwargs) -> subprocess.Popen:
    """:class:`subprocess.Popen`, started in the calling thread's tree if it has one.

    Raises :class:`ProcessTreeKilledError` instead of starting anything once that
    tree has been killed.
    """
    tree = current()
    if tree is None:
        return subprocess.Popen(args, **kwargs)  # noqa: S603 - the caller's command
    if tree.killed:
        what = args if isinstance(args, str | bytes | os.PathLike) else args[0]
        raise ProcessTreeKilledError(f"not starting {what!r}: the job was stopped")
    proc = subprocess.Popen(args, **{**tree.popen_kwargs(), **kwargs})  # noqa: S603
    tree.adopt(proc)
    return proc
