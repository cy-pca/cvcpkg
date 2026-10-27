"""A build job's processes are stopped as a whole (cvcpkg.proctree).

The builder kills a job the server is done with, and "the job" is every
process its build script started: on POSIX the script's process group, on
Windows a Job Object (plus ``taskkill /T``), which also holds a process whose
parent has already exited.  These tests start real process trees -- a child
that starts a grandchild -- and check that a kill leaves none of them running.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cvcpkg import proctree

# The grandchild records its own pid: on Windows a venv's python.exe is a
# launcher that runs the real interpreter as a child, so Popen.pid would be
# the launcher's.
_GRANDCHILD = (
    "import os, sys, time; open(sys.argv[1], 'w').write(str(os.getpid())); time.sleep(120)"
)
_CHILD = """
import subprocess, sys, time
subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2]])
if sys.argv[3] == "stay":
    time.sleep(120)
"""


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        handle = k32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED
        if not handle:
            return False
        try:
            return k32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102  # WAIT_TIMEOUT
        finally:
            k32.CloseHandle(ctypes.c_void_p(handle))
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # A zombie still answers signal 0; only a reaped one is gone.  The
    # grandchild's parent is init by now, which reaps it promptly.
    return True


def _wait_for(predicate, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _start_tree(tree: proctree.ProcessTree, tmp_path: Path, child: str) -> tuple:
    """Start a child that starts a grandchild, in *tree*; return (proc, grandchild pid)."""
    pidfile = tmp_path / "grandchild.pid"
    with proctree.bound(tree):
        proc = proctree.popen(
            [sys.executable, "-c", _CHILD, _GRANDCHILD, str(pidfile), child],
            stdin=subprocess.DEVNULL,
        )
    assert _wait_for(lambda: pidfile.is_file() and pidfile.read_text().strip()), "no grandchild"
    return proc, int(pidfile.read_text())


def test_kill_stops_the_script_and_what_it_started(tmp_path):
    tree = proctree.ProcessTree("probe")
    proc, grandchild = _start_tree(tree, tmp_path, "stay")
    try:
        assert proc.poll() is None and _alive(grandchild)
        if sys.platform != "win32":
            # The script leads its own group; the grandchild is in it.
            assert os.getpgid(proc.pid) == proc.pid
            assert os.getpgid(grandchild) == proc.pid

        tree.kill(grace=2.0)

        assert proc.wait(timeout=20) != 0
        assert _wait_for(lambda: not _alive(grandchild)), "the grandchild outlived the kill"
    finally:
        tree.close()


def test_kill_reaches_a_process_whose_parent_already_exited(tmp_path):
    """The script exits and leaves its child running: an orphan that a walk by
    parent pid (``taskkill /T``) no longer finds.  Its process group (POSIX)
    or Job Object (Windows) still holds it."""
    tree = proctree.ProcessTree("probe")
    proc, grandchild = _start_tree(tree, tmp_path, "leave")
    try:
        # The child exits on its own; returncode stays unread, as it does
        # while a build's output pipe is still held open by the orphan.
        assert _wait_for(lambda: not _alive(proc.pid) or _is_zombie(proc.pid))
        assert _alive(grandchild)

        tree.kill(grace=2.0)

        assert _wait_for(lambda: not _alive(grandchild)), "the orphan outlived the kill"
    finally:
        tree.close()
        proc.wait(timeout=20)


def _is_zombie(pid: int) -> bool:
    """POSIX: *pid* exited and waits to be reaped (Linux/BSD ``ps`` state Z)."""
    if sys.platform == "win32":
        return False
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return out.stdout.strip().startswith("Z")


def test_a_killed_tree_starts_nothing(tmp_path):
    tree = proctree.ProcessTree("probe")
    tree.kill()
    with proctree.bound(tree), pytest.raises(proctree.ProcessTreeKilledError):
        proctree.popen([sys.executable, "-c", "pass"])
    tree.close()


def test_without_a_tree_popen_is_plain_popen():
    """Outside a fleet builder's job thread (``cvcpkg build``) nothing changes."""
    assert proctree.current() is None
    if sys.platform == "win32":
        assert proctree.popen([sys.executable, "-c", "pass"]).wait(timeout=60) == 0
        return
    proc = proctree.popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        # Not moved into a session of its own: still in this process's group.
        assert os.getpgid(proc.pid) == os.getpgrp()
    finally:
        proc.kill()
        proc.wait(timeout=60)


def test_binding_is_per_thread_and_restored():
    import threading

    tree = proctree.ProcessTree()
    seen: list = []
    with proctree.bound(tree):
        assert proctree.current() is tree
        t = threading.Thread(target=lambda: seen.append(proctree.current()))
        t.start()
        t.join()
    assert seen == [None], "another thread's processes must not join this tree"
    assert proctree.current() is None
