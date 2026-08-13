"""Build-tree liveness: the heartbeat cvcpkg publishes and the reaper reading it.

Both halves of the 2026-08-04 incident are pinned here.  The deploy workflow's
disk cleanup deleted /tmp/cvcpkg-haiku-image-b6wd0qrn 90 minutes into a build
because its predicate was

    find /tmp -maxdepth 1 -name 'cvcpkg-*' -type d -mmin +60 -exec rm -rf {} +

and ``-maxdepth 1 -type d -mmin`` tests the TOP directory's OWN mtime, which
stops advancing the moment a build tree has created build/ and install/.  The
first test below is that filesystem fact, stated directly, so nobody has to
take it on faith again.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from cvcpkg.heartbeat import (
    HEARTBEAT_NAME,
    beat_once,
    read_heartbeat,
    unwatch,
    watch,
    watched,
)

REAPER = Path(__file__).resolve().parents[2] / "scripts" / "reap-stale-build-dirs.sh"


def _backdate(path: Path, seconds: float) -> None:
    """Backdate every entry in *path*, deepest first.

    Plain ``os.utime`` (following symlinks) on purpose: the fixture trees
    contain no symlinks, and ``follow_symlinks=False`` raises
    ``NotImplementedError`` for ``utime`` on Windows.
    """
    old = time.time() - seconds
    for p in sorted(path.rglob("*"), key=lambda q: len(q.parts), reverse=True):
        os.utime(p, (old, old))
    os.utime(path, (old, old))


def _build_tree(root: Path, name: str) -> Path:
    """A tree shaped like a real build: shallow dirs, deep files."""
    d = root / name
    (d / "build" / "CMakeFiles" / "foo.dir" / "src").mkdir(parents=True)
    (d / "install").mkdir()
    (d / "build" / "CMakeFiles" / "foo.dir" / "src" / "a.o").write_bytes(b"x" * 512)
    return d


def _reap(*args: str) -> str:
    """Run the reaper the way the workflow does: POSIX sh, script on stdin."""
    proc = subprocess.run(
        ["sh", "-s", "--", *args],
        stdin=REAPER.open("rb"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


class TestTheFilesystemFactBehindTheBug:
    def test_deep_writes_do_not_advance_the_top_directory_mtime(self, tmp_path):
        # This is the whole root cause.  A directory's mtime changes only when
        # an entry is added or removed DIRECTLY inside it, so a build tree
        # looks frozen from the moment its second-level dirs exist, no matter
        # how many thousands of files it writes further down.
        tree = tmp_path / "cvcpkg-haiku-image-b6wd0qrn"
        (tree / "build").mkdir(parents=True)
        top_mtime = tree.stat().st_mtime

        deep = tree / "build" / "CMakeFiles" / "foo.dir" / "src"
        deep.mkdir(parents=True)
        for i in range(50):
            (deep / f"file_{i}.o").write_bytes(b"x")

        assert (
            tree.stat().st_mtime == top_mtime
        ), "if this ever fails the bug's premise changed; re-derive the fix"
        newest = max(p.stat().st_mtime for p in tree.rglob("*"))
        assert newest > top_mtime


class TestHeartbeatFile:
    def test_beat_records_who_is_working_and_refreshes_the_root(self, tmp_path):
        tree = _build_tree(tmp_path, "cvcpkg-llvm18-aaa")
        _backdate(tree, 99999)
        frozen = tree.stat().st_mtime

        assert beat_once(tree, label="llvm18") is True

        assert tree.stat().st_mtime > frozen, "the root's mtime is the cross-user signal"
        hb = read_heartbeat(tree)
        assert hb["pid"] == str(os.getpid())
        assert hb["label"] == "llvm18"
        assert hb["host"]

    def test_beat_on_a_deleted_tree_reports_gone_rather_than_raising(self, tmp_path):
        # How the watcher thread learns to forget a root, and why a racing
        # rm -rf can never turn into a build failure.
        assert beat_once(tmp_path / "never-existed") is False

    def test_no_partial_file_is_ever_visible(self, tmp_path):
        # Written via rename, so a reaper reading concurrently sees either the
        # previous heartbeat or the new one, never half of one.
        tree = _build_tree(tmp_path, "cvcpkg-zlib-bbb")
        beat_once(tree, label="zlib")
        beat_once(tree, label="zlib")
        assert not (tree / f"{HEARTBEAT_NAME}.tmp").exists()
        assert read_heartbeat(tree)["label"] == "zlib"


class TestWatcher:
    def test_watch_keeps_beating_and_unwatch_stops(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CVCPKG_HEARTBEAT_INTERVAL", "0.05")
        tree = _build_tree(tmp_path, "cvcpkg-slow-ccc")
        _backdate(tree, 99999)

        # Liveness is probed via the heartbeat FILE's mtime_ns: every beat
        # rewrites it via os.replace, so one beat changes it.  The recorded
        # ``beat=`` value is integer SECONDS — comparing it needs the wall
        # clock to roll a second AND the thread to be scheduled in time,
        # which flaked on a loaded macOS runner (equal after a 5 s window).
        hb = tree / HEARTBEAT_NAME
        watch(tree, label="slow")
        try:
            first = hb.stat().st_mtime_ns
            deadline = time.time() + 10
            while time.time() < deadline and hb.stat().st_mtime_ns == first:
                time.sleep(0.05)
            assert hb.stat().st_mtime_ns != first, "the watcher stopped beating"
        finally:
            unwatch(tree)

        # Drain first: a periodic beat already past the membership re-check
        # when unwatch() returned may still land within microseconds.  Settle
        # AFTER that window, then require silence.
        time.sleep(0.1)
        settled = hb.stat().st_mtime_ns
        time.sleep(0.3)
        assert hb.stat().st_mtime_ns == settled, "unwatch must stop the beats"

    def test_watch_wakes_a_parked_thread_for_the_new_root(self, tmp_path, monkeypatch):
        # The shared thread parks in an interval wait between beats.  A root
        # watched MID-WAIT must not sit beatless until that wait expires: in
        # the full suite an earlier test left a thread parked on the default
        # 60 s interval and a freshly watched tree got zero beats for the
        # whole assertion window (macos-latest, py3.10 and py3.12).  watch()
        # must wake the thread, which also makes it re-read the interval.
        monkeypatch.setenv("CVCPKG_HEARTBEAT_INTERVAL", "60")
        parked = _build_tree(tmp_path, "cvcpkg-parked-eee")
        watch(parked, label="parked")
        try:
            time.sleep(0.2)  # let the thread reach its 60 s wait
            monkeypatch.setenv("CVCPKG_HEARTBEAT_INTERVAL", "0.05")
            fresh = _build_tree(tmp_path, "cvcpkg-fresh-fff")
            hb = fresh / HEARTBEAT_NAME
            watch(fresh, label="fresh")
            try:
                first = hb.stat().st_mtime_ns
                deadline = time.time() + 10
                while time.time() < deadline and hb.stat().st_mtime_ns == first:
                    time.sleep(0.05)
                assert (
                    hb.stat().st_mtime_ns != first
                ), "a root watched while the thread was parked never got a beat"
            finally:
                unwatch(fresh)
        finally:
            unwatch(parked)

    def test_one_thread_serves_every_watched_tree(self, tmp_path, monkeypatch):
        # The builder daemon runs thousands of jobs in one process; a thread
        # per job would be an unbounded leak.
        import threading

        monkeypatch.setenv("CVCPKG_HEARTBEAT_INTERVAL", "0.05")
        trees = [_build_tree(tmp_path, f"cvcpkg-job-{i}") for i in range(8)]
        before = {t for t in threading.enumerate() if t.name == "cvcpkg-heartbeat"}
        for t in trees:
            watch(t, label=t.name)
        try:
            live = {t for t in threading.enumerate() if t.name == "cvcpkg-heartbeat"}
            assert len(live - before) <= 1
        finally:
            for t in trees:
                unwatch(t)

    def test_watched_context_manager_publishes_then_releases(self, tmp_path):
        tree = _build_tree(tmp_path, "cvcpkg-ctx-ddd")
        with watched(tree, label="ctx"):
            assert read_heartbeat(tree)["label"] == "ctx"
        # Still present after the build: the final beat is what dates the tree
        # for a reaper if the process dies before cleaning up.
        assert (tree / HEARTBEAT_NAME).exists()


@pytest.mark.skipif(not REAPER.exists(), reason="reaper script not present")
@pytest.mark.skipif(
    os.name == "nt",
    reason="the reaper script runs on the POSIX build hosts (deploy-prod ssh cleanup); "
    "Windows builders never execute it, and Git Bash's sh cannot see Windows pids",
)
class TestReaperScript:
    def test_spares_an_in_flight_build_and_reaps_an_abandoned_one(self, tmp_path):
        # The incident, end to end, through the exact delivery the workflow
        # uses (`sh -s --` with the script streamed on stdin).
        live = _build_tree(tmp_path, "cvcpkg-haiku-image-b6wd0qrn")
        _backdate(live, 99999)
        # Only the deep file is current: the top mtime still says "stale", the
        # way an in-flight build genuinely looks.
        deep = live / "build" / "CMakeFiles" / "foo.dir" / "src" / "a.o"
        os.utime(deep, None)

        dead = _build_tree(tmp_path, "cvcpkg-abandoned-eee")
        _backdate(dead, 99999)

        out = _reap("-a", "60", "-d", "1", str(tmp_path))

        assert live.exists(), f"reaped an in-flight build:\n{out}"
        assert not dead.exists(), f"failed to reap an abandoned tree:\n{out}"

    def test_heartbeat_alone_saves_a_tree_with_no_filesystem_activity(self, tmp_path):
        quiet = _build_tree(tmp_path, "cvcpkg-linking-fff")
        _backdate(quiet, 99999)
        beat_once(quiet, label="linking")
        # Backdate the root again so ONLY the heartbeat file is current.
        old = time.time() - 99999
        os.utime(quiet, (old, old))

        out = _reap("-a", "60", "-d", "1", str(tmp_path))

        assert quiet.exists(), f"a fresh heartbeat must protect a quiet build:\n{out}"

    def test_live_pid_saves_a_tree_whose_heartbeat_went_stale(self, tmp_path):
        # A build whose heartbeat thread wedged four hours ago while the build
        # itself carried on: every mtime in the tree is stale, and only the
        # recorded pid still says somebody is home.
        tree = _build_tree(tmp_path, "cvcpkg-wedged-ggg")
        beat_once(tree, label="wedged")
        _backdate(tree, 4 * 3600)

        out = _reap("-a", "60", "-d", "1", str(tmp_path))

        assert tree.exists(), f"the recorded pid ({os.getpid()}) is alive:\n{out}"

    def test_a_pid_is_not_believed_forever(self, tmp_path):
        # The other side of that bargain.  Builders are long-lived and every
        # cvcpkg process is `python3`, so eventually the pid space wraps and a
        # stranded heartbeat points at an unrelated live process.  Past the
        # trust window the tree is judged on mtime alone, or it would be
        # immortal and refill the disk this step exists to protect.
        tree = _build_tree(tmp_path, "cvcpkg-stranded-hhh")
        beat_once(tree, label="stranded")  # records THIS pytest process, alive
        _backdate(tree, 4 * 3600)

        # Inside the window (-P 1440 = 24h by default): spared.
        _reap("-a", "60", "-d", "1", str(tmp_path))
        assert tree.exists()

        # Same tree, same live pid, but the heartbeat is now older than the
        # window: reaped.
        out = _reap("-a", "60", "-P", "60", "-d", "1", str(tmp_path))
        assert not tree.exists(), f"a stale pid must stop protecting a tree:\n{out}"

    def test_never_deletes_a_work_root_or_the_recipe_cache(self, tmp_path):
        # --work-dir /tmp/cvcpkg-builder matches 'cvcpkg-*' and is always older
        # than the threshold.  Deleting it left builders unable to write their
        # pidfile; four went down that way once already.
        for name in ("cvcpkg-builder", "cvcpkg-recipe-cache"):
            (tmp_path / name).mkdir()
            _backdate(tmp_path / name, 99999)

        _reap("-a", "60", "-d", "1", str(tmp_path))

        assert (tmp_path / "cvcpkg-builder").is_dir()
        assert (tmp_path / "cvcpkg-recipe-cache").is_dir()

    def test_never_deletes_a_fleet_container_holding_job_dirs(self, tmp_path):
        # The fleet supervisor names each per-server work dir after the server,
        # so cvcpkg.org's container is literally 'cvcpkg-org' — deleting it
        # would take every live job on that server with it.
        slug = tmp_path / "cvcpkg-org"
        job = _build_tree(slug, "cvcpkg-job-live-hhh")
        _backdate(slug, 99999)

        _reap("-a", "60", "-d", "1", str(tmp_path))

        assert slug.is_dir() and job.is_dir()

    def test_depth_2_reaches_the_fleet_supervisor_layout(self, tmp_path):
        # /var/lib/cvcpkg-builder/<server-slug>/cvcpkg-job-*: a depth-1 sweep
        # silently cleans nothing on a converted host.
        slug = tmp_path / "cvcpkg-org"
        dead = _build_tree(slug, "cvcpkg-job-dead-iii")
        _backdate(tmp_path, 99999)

        _reap("-a", "60", "-d", "2", str(tmp_path))

        assert not dead.exists()
        assert slug.is_dir(), "the per-server container must survive"

    def test_dry_run_deletes_nothing(self, tmp_path):
        dead = _build_tree(tmp_path, "cvcpkg-abandoned-jjj")
        _backdate(dead, 99999)

        out = _reap("-n", "-a", "60", "-d", "1", str(tmp_path))

        assert dead.exists()
        assert "would reap" in out

    def test_a_missing_root_is_not_an_error(self, tmp_path):
        _reap("-a", "60", "-d", "1", str(tmp_path / "nope"))
