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

from cvcpkg.builder_gc import sweep_cache, sweep_work_dir


def _job_dir(root: Path, name: str, *, size: int = 1024, age_seconds: float = 0) -> Path:
    """Create a job-dir-shaped tree with a payload file, optionally backdated."""
    d = root / name
    (d / "build").mkdir(parents=True)
    (d / "build" / "blob").write_bytes(b"x" * size)
    if age_seconds:
        old = time.time() - age_seconds
        os.utime(d, (old, old))
    return d


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
