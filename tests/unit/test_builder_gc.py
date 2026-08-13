"""Builder disk reclamation — orphaned job scratch dirs and the download cache.

The leak these sweeps exist for: ``_execute_job`` removes its job root in a
``finally``, which never runs when the builder is killed mid-build (deploy
restart, SIGKILL, OOM).  On the dev cluster that stranded 28 GiB and failed
three CUDA builds with ENOSPC (2026-08-02).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from cvcpkg.builder_gc import sweep_cache, sweep_work_dir


def _job_dir(
    root: Path,
    name: str,
    *,
    size: int = 1024,
    age_seconds: float = 0,
    top_only: bool = False,
) -> Path:
    """Create a job-dir-shaped tree with a payload file, optionally backdated.

    ``age_seconds`` backdates the WHOLE tree, because that is what an abandoned
    tree actually looks like.  ``top_only=True`` backdates only the top
    directory and leaves the contents current — the shape of an *in-flight*
    build, whose root mtime froze the moment ``build/`` was created while the
    real work went on several levels down.  Ageing only the top used to be this
    helper's sole behaviour, which is exactly why the sweep's own tests used to
    agree with the bug.
    """
    d = root / name
    (d / "build").mkdir(parents=True)
    (d / "build" / "blob").write_bytes(b"x" * size)
    if age_seconds:
        old = time.time() - age_seconds
        targets = [d] if top_only else [d / "build" / "blob", d / "build", d]
        for target in targets:
            os.utime(target, (old, old))
    return d


def _require_symlinks(tmp_path: Path) -> None:
    """Skip the caller when this process may not create symlinks.

    On Windows os.symlink needs SeCreateSymbolicLinkPrivilege (elevation or
    Developer Mode) and raises OSError WinError 1314 otherwise.  CI runners
    have it, so the assertions below still run there — only unprivileged dev
    boxes skip.  A dangling *file* link is enough to probe the privilege: it
    is the same check the directory link uses, and it unlinks cleanly on
    Windows (os.unlink cannot remove a directory symlink there).
    """
    probe = tmp_path / ".symlink-probe"
    try:
        probe.symlink_to("probe-target")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation requires elevated privileges on Windows")
    probe.unlink()


class TestSweepWorkDir:
    def test_startup_sweep_removes_all_job_dirs(self, tmp_path):
        a = _job_dir(tmp_path, "cvcpkg-job-zlib-aaa")
        b = _job_dir(tmp_path, "cvcpkg-job-hdf5-bbb")
        res = sweep_work_dir(tmp_path)  # max_age 0 == everything
        assert res.removed == 2
        assert not a.exists() and not b.exists()
        assert res.freed_bytes > 0

    def test_never_deletes_the_work_root_convention(self, tmp_path):
        # --work-dir /tmp/cvcpkg-builder means a naive cvcpkg-* glob in /tmp
        # eats the very directory it is meant to clean.
        root = tmp_path / "cvcpkg-builder"
        root.mkdir()
        (root / "keepme").write_text("x")
        _job_dir(tmp_path, "cvcpkg-job-zlib-aaa")
        res = sweep_work_dir(tmp_path)
        assert res.removed == 1
        assert root.is_dir() and (root / "keepme").exists()

    def test_finds_job_dirs_in_the_fleet_layout(self, tmp_path):
        # The fleet supervisor nests one level: work_dir/<server-slug>/job...
        # and slugs the server HOST, so cvcpkg.org's container is literally
        # named "cvcpkg-org" — which the legacy bare cvcpkg-* pattern matches.
        # Deleting it would take a live job root down with it.
        slug = tmp_path / "cvcpkg-org"
        slug.mkdir()
        nested = _job_dir(slug, "cvcpkg-job-zlib-aaa")
        res = sweep_work_dir(tmp_path)
        assert res.removed == 1
        assert not nested.exists()
        assert slug.is_dir(), "the per-server container itself must survive"

    def test_container_survives_while_its_live_job_is_kept(self, tmp_path):
        # The dangerous composition: an old per-server container holding an
        # in-flight job.  Sweeping the container would delete the running
        # build even though its root was passed in `keep`.
        slug = tmp_path / "cvcpkg-org"
        slug.mkdir()
        old = time.time() - 99999
        os.utime(slug, (old, old))
        live = _job_dir(slug, "cvcpkg-job-live-aaa", age_seconds=99999)
        res = sweep_work_dir(tmp_path, max_age_seconds=60, keep=[live])
        assert res.removed == 0
        assert live.exists() and (live / "build" / "blob").exists()
        assert slug.is_dir()

    def test_legacy_bare_scratch_dir_is_still_swept(self, tmp_path):
        # The pre-job-root layout (/tmp/cvcpkg-<recipe>-<rand>) must still be
        # reclaimed — it has no job-root children, so it is not a container.
        legacy = _job_dir(tmp_path, "cvcpkg-openssl-bl5xer5w")
        res = sweep_work_dir(tmp_path)
        assert res.removed == 1
        assert not legacy.exists()

    def test_age_gate_spares_recent_dirs(self, tmp_path):
        fresh = _job_dir(tmp_path, "cvcpkg-job-fresh-aaa")
        stale = _job_dir(tmp_path, "cvcpkg-job-stale-bbb", age_seconds=7200)
        res = sweep_work_dir(tmp_path, max_age_seconds=3600)
        assert res.removed == 1
        assert fresh.exists() and not stale.exists()

    def test_keep_protects_an_in_flight_job(self, tmp_path):
        live = _job_dir(tmp_path, "cvcpkg-job-live-aaa", age_seconds=99999)
        dead = _job_dir(tmp_path, "cvcpkg-job-dead-bbb", age_seconds=99999)
        res = sweep_work_dir(tmp_path, max_age_seconds=60, keep=[live])
        assert res.removed == 1
        assert live.exists(), "a running build must never be swept"
        assert not dead.exists()

    def test_ignores_unrelated_directories(self, tmp_path):
        mine = _job_dir(tmp_path, "cvcpkg-job-zlib-aaa")
        (tmp_path / "someone-elses-data").mkdir()
        (tmp_path / "cvcpkg-recipe-cache").mkdir()
        res = sweep_work_dir(tmp_path)
        assert res.removed == 1
        assert not mine.exists()
        assert (tmp_path / "someone-elses-data").is_dir()
        assert (tmp_path / "cvcpkg-recipe-cache").is_dir()

    def test_in_flight_build_survives_however_long_it_runs(self, tmp_path):
        # The 2026-08-04 incident, in miniature.  A build tree's TOP directory
        # stops being modified as soon as build/ and install/ exist, so judging
        # by the top mtime alone reaps a working tree — and raising the
        # threshold only moves the cliff, since llvm18 alone declares
        # timeout_seconds: 18000 (5 hours).
        live = _job_dir(
            tmp_path, "cvcpkg-job-haiku-image-b6wd0qrn", age_seconds=99999, top_only=True
        )
        dead = _job_dir(tmp_path, "cvcpkg-job-dead-bbb", age_seconds=99999)

        res = sweep_work_dir(tmp_path, max_age_seconds=3600)

        assert live.exists(), "an in-flight build must never be reaped"
        assert not dead.exists(), "a genuinely abandoned tree must still be reaped"
        assert res.removed == 1

    def test_heartbeat_spares_a_tree_whose_every_mtime_is_stale(self, tmp_path):
        # A build can be busy without touching the filesystem at all — a long
        # link, a long test, a VM booting.  The heartbeat is the signal that
        # covers that gap, and it is the only one an out-of-process reaper can
        # rely on when the tree is quiet.
        from cvcpkg.heartbeat import beat_once

        quiet = _job_dir(tmp_path, "cvcpkg-job-linking-ccc", age_seconds=99999)
        beat_once(quiet, label="linking")
        # Backdate the root itself so ONLY the heartbeat file looks current;
        # this proves the heartbeat is doing the work, not the root's mtime.
        old = time.time() - 99999
        os.utime(quiet, (old, old))

        res = sweep_work_dir(tmp_path, max_age_seconds=3600)

        assert quiet.exists(), "a fresh heartbeat must protect a quiet build"
        assert res.removed == 0

    def test_dead_process_heartbeat_does_not_protect_forever(self, tmp_path):
        # The other direction: a heartbeat left behind by a process that died
        # must not make its tree immortal, or builders fill their disks again.
        from cvcpkg.heartbeat import HEARTBEAT_NAME, beat_once

        stranded = _job_dir(tmp_path, "cvcpkg-job-killed-ddd")
        beat_once(stranded, label="killed")
        # A pid that cannot be running: 0 is never a normal process, and the
        # host no longer matches either.
        (stranded / HEARTBEAT_NAME).write_text("pid=0\nhost=some-other-builder\n", encoding="utf-8")
        old = time.time() - 99999
        for target in (
            stranded / "build" / "blob",
            stranded / "build",
            stranded / HEARTBEAT_NAME,
            stranded,
        ):
            os.utime(target, (old, old))

        res = sweep_work_dir(tmp_path, max_age_seconds=3600)

        assert not stranded.exists()
        assert res.removed == 1

    def test_a_live_pid_stops_protecting_once_its_heartbeat_is_ancient(self, tmp_path):
        # Every cvcpkg process is `python3` and builders live for weeks, so the
        # pid space eventually wraps and a stranded heartbeat starts naming an
        # unrelated live process.  Believing it forever would make the tree
        # immortal, which is how disks fill.
        from cvcpkg.builder_gc import _PID_TRUST_SECONDS
        from cvcpkg.heartbeat import HEARTBEAT_NAME, beat_once

        stranded = _job_dir(tmp_path, "cvcpkg-job-wrapped-eee")
        beat_once(stranded, label="wrapped")  # records THIS process, alive
        # The extra 60s is load-bearing, not padding.  The gate is
        # `hb_mtime >= cutoff - _PID_TRUST_SECONDS`, and `cutoff` comes from a
        # *second* time.time() call below, so without slack this clears the
        # boundary by exactly the drift between two adjacent clock reads.  On
        # Linux that is ~100µs and it passes; on Windows time.time() is coarse
        # (~15.6ms ticks before CPython 3.13), both reads land in the same tick,
        # the margin is exactly 0.0, and `>=` judges the ancient heartbeat still
        # trustworthy -- windows-latest only.  Deriving both from one hoisted
        # `now` does NOT fix it: that makes the margin 0 by construction and
        # fails everywhere.
        old = time.time() - (_PID_TRUST_SECONDS + 3600) - 60
        for target in (
            stranded / "build" / "blob",
            stranded / "build",
            stranded / HEARTBEAT_NAME,
            stranded,
        ):
            os.utime(target, (old, old))

        # The behaviour under test is the TRUST WINDOW: an ancient heartbeat
        # must stop protecting the tree, i.e. the sweep must judge it stale.
        # Asserted BEFORE the sweep runs: deletion itself advances the parent
        # directory's mtime as entries disappear (the very premise of this
        # module), so a post-sweep _is_active on a partially deleted or
        # delete-pending tree can read "active" again -- observed on
        # windows-latest in round 8 of pr-443.
        from cvcpkg.builder_gc import _LAST_RMTREE_ERRORS, _is_active

        cutoff = time.time() - 3600
        assert not _is_active(stranded, cutoff), "a pid outlives its credibility"

        res = sweep_work_dir(tmp_path, max_age_seconds=3600)

        if os.name == "nt" and stranded.exists():
            # NTFS can defeat same-pass deletion regardless of the decision.
            # Both tombstone shapes were observed live on windows-latest
            # (pr-443 rounds 3-7): with a scanner holding share-delete
            # handles, every unlink/rmdir can SUCCEED with zero errors
            # recorded while the whole tree keeps listing (delete-pending
            # entries retain their directory slot until the last handle
            # closes) -- or the parent rmdir fails DIR_NOT_EMPTY over the
            # pending children.  Either way the sweep has done all the OS
            # allows; the space is reclaimed when the handle closes, and a
            # periodic sweep picks up any remnant next pass.  Only the
            # accounting is asserted here: the tree was not counted as
            # removed while it still lists.
            assert res.removed == 0, _LAST_RMTREE_ERRORS
        else:
            assert not stranded.exists(), (
                f"sweep judged the tree stale but it survived: "
                f"rmtree_errors={_LAST_RMTREE_ERRORS}"
            )
            assert res.removed == 1

    def test_dry_run_reports_without_deleting(self, tmp_path):
        d = _job_dir(tmp_path, "cvcpkg-job-zlib-aaa", size=4096)
        res = sweep_work_dir(tmp_path, dry_run=True)
        assert res.removed == 1
        assert res.freed_bytes >= 4096
        assert d.exists()

    def test_missing_work_dir_is_not_an_error(self, tmp_path):
        res = sweep_work_dir(tmp_path / "nope")
        assert res.removed == 0 and not res

    def test_does_not_follow_symlinks_out_of_the_work_dir(self, tmp_path):
        _require_symlinks(tmp_path)
        outside = tmp_path / "precious"
        outside.mkdir()
        (outside / "data").write_text("do not delete")
        work = tmp_path / "work"
        work.mkdir()
        (work / "cvcpkg-job-evil-aaa").symlink_to(outside, target_is_directory=True)
        res = sweep_work_dir(work)
        assert res.removed == 0
        assert (outside / "data").read_text() == "do not delete"


class TestSweepCache:
    def _entry(self, cache: Path, sha: str, *, age_seconds: float = 0) -> Path:
        d = cache / sha
        d.mkdir(parents=True)
        (d / "archive.tar.gz").write_bytes(b"y" * 2048)
        if age_seconds:
            old = time.time() - age_seconds
            os.utime(d, (old, old))
        return d

    def test_prunes_only_old_entries(self, tmp_path):
        fresh = self._entry(tmp_path, "aa" * 32)
        stale = self._entry(tmp_path, "bb" * 32, age_seconds=60 * 60 * 24 * 30)
        res = sweep_cache(tmp_path, max_age_seconds=60 * 60 * 24 * 14)
        assert res.removed == 1
        assert fresh.exists() and not stale.exists()

    def test_zero_max_age_disables_rather_than_wiping(self, tmp_path):
        # "0 == off" matches the server's retention knobs; the other reading
        # (delete everything) would be a footgun in a periodic loop.
        e = self._entry(tmp_path, "cc" * 32, age_seconds=99999999)
        res = sweep_cache(tmp_path, max_age_seconds=0)
        assert res.removed == 0
        assert e.exists()

    def test_missing_cache_is_not_an_error(self, tmp_path):
        res = sweep_cache(tmp_path / "nope", max_age_seconds=1)
        assert res.removed == 0
