"""Unit tests for the server storage migration + doctor.

These prove the migration *mechanism* and the doctor's detect/heal logic
against a backend of a DIFFERENT type from the source — an in-process
``mem://`` backend registered for the test — so no S3/network/dep is needed.
The exact same code path runs against a real ``s3://`` Garage backend (see
``tests/integration/test_storage_migration_s3.py``).

Covered:
  * file:// → mem:// migration copies every archive, SHA-256 verified on read
    AND on the written object, and flips the persisted storage_uri;
  * integrity: a source archive whose bytes don't match the catalog is caught
    and the migration refuses to flip the backend;
  * resume: a re-run skips already-verified archives;
  * doctor detects missing / corrupt archives and an incomplete migration;
  * doctor --heal restores missing/corrupt archives from a source backend and
    resumes an interrupted migration, and reports genuinely unhealable ones.
"""

from __future__ import annotations

import hashlib
import io

import pytest
import yaml

from cvcpkg import storage
from cvcpkg.server import archive_store
from cvcpkg.server import storage_doctor as doc
from cvcpkg.server.storage_migration import (
    MigrationJournal,
    iter_catalog_archives,
    run_migration,
)

# ── An in-process backend of a different "type" than file:// ────


class MemoryBackend:
    """Minimal object store keeping bytes in a dict, keyed by full URI."""

    schemes = ("mem",)

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def head(self, uri: str):
        if uri not in self.objects:
            raise FileNotFoundError(uri)
        return storage.ObjectInfo(size=len(self.objects[uri]))

    def open(self, uri: str):
        if uri not in self.objects:
            raise FileNotFoundError(uri)
        return io.BytesIO(self.objects[uri])

    def supports_range(self, uri: str) -> bool:
        return False

    def put(self, uri: str, data, size: int = -1) -> None:
        self.objects[uri] = data.read()

    def list(self, uri: str):
        for k in list(self.objects):
            if k.startswith(uri):
                yield k[len(uri) :]


@pytest.fixture
def mem_backend():
    be = MemoryBackend()
    storage.register(be)  # registers for the "mem" scheme
    try:
        yield be
    finally:
        storage._registry.pop("mem", None)


# ── Helpers ─────────────────────────────────────────────────────


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _populate(state_dir, archives: dict[str, bytes]) -> None:
    """Seed a file:// server state dir: archives on disk + a YAML catalog."""
    adir = state_dir / archive_store.ARCHIVES_SUBDIR
    adir.mkdir(parents=True, exist_ok=True)
    bundles = []
    for fn, data in archives.items():
        (adir / fn).write_bytes(data)
        bundles.append(
            {
                "name": fn.split("-")[0],
                "version": "1.0.0",
                "platform": "linux",
                "arch": "x86_64",
                "build_type": "release",
                "link": "shared",
                "sha256": _sha(data),
                "size_bytes": len(data),
                "archive_url": f"/v1/download/{fn}",
            }
        )
    (state_dir / "index.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "revision": 1, "bundles": bundles})
    )


_ARCHIVES = {
    "alpha-1.0.0-linux-x86_64-release-shared.tar.zst": b"alpha-bytes" * 4096,
    "beta-1.0.0-linux-x86_64-release-shared.tar.zst": b"beta!" * 100,
    "gamma-1.0.0-linux-x86_64-release-shared.tar.zst": bytes(range(256)) * 50,
}


# ── Tests ───────────────────────────────────────────────────────


def test_catalog_enumeration_reads_index(tmp_path):
    _populate(tmp_path, _ARCHIVES)
    refs = {r.filename: r for r in iter_catalog_archives(tmp_path)}
    assert set(refs) == set(_ARCHIVES)
    for fn, data in _ARCHIVES.items():
        assert refs[fn].sha256 == _sha(data)
        assert refs[fn].size == len(data)


def test_migrate_file_to_mem_flips_and_verifies(tmp_path, mem_backend):
    _populate(tmp_path, _ARCHIVES)
    result = run_migration(tmp_path, "mem://vol", deep_verify=True)

    assert result.ok
    assert result.flipped
    assert result.migrated == len(_ARCHIVES)
    assert result.failures == []

    # Every archive landed in the mem backend with the right bytes.
    for fn, data in _ARCHIVES.items():
        assert mem_backend.objects[f"mem://vol/archives/{fn}"] == data

    # The active backend was switched and persisted.
    assert archive_store.load_storage_uri(tmp_path, f"file://{tmp_path}") == "mem://vol"

    # Journal marks completion.
    journal = MigrationJournal.load(tmp_path)
    assert journal is not None and journal.completed
    assert all(journal.is_verified(fn) for fn in _ARCHIVES)


def test_migrate_refuses_same_backend(tmp_path):
    _populate(tmp_path, _ARCHIVES)
    from cvcpkg.server.storage_migration import MigrationError

    with pytest.raises(MigrationError):
        run_migration(tmp_path, f"file://{tmp_path}")


def test_migrate_detects_source_corruption(tmp_path, mem_backend):
    _populate(tmp_path, _ARCHIVES)
    # Corrupt one archive on disk so its bytes no longer match the catalog sha.
    victim = next(iter(_ARCHIVES))
    (tmp_path / archive_store.ARCHIVES_SUBDIR / victim).write_bytes(b"tampered")

    result = run_migration(tmp_path, "mem://vol", deep_verify=True)

    assert not result.ok
    assert not result.flipped  # backend NOT switched while integrity is broken
    assert any(fn == victim for fn, _ in result.failures)
    # The healthy archives still copied.
    assert result.migrated == len(_ARCHIVES) - 1
    # storage_uri stays on the source.
    assert archive_store.load_storage_uri(tmp_path, f"file://{tmp_path}") == f"file://{tmp_path}"


def test_migrate_resume_skips_verified(tmp_path, mem_backend):
    _populate(tmp_path, _ARCHIVES)
    # First pass fails one archive (corrupt), so migration is incomplete.
    victim = "beta-1.0.0-linux-x86_64-release-shared.tar.zst"
    good = (tmp_path / archive_store.ARCHIVES_SUBDIR / victim).read_bytes()
    (tmp_path / archive_store.ARCHIVES_SUBDIR / victim).write_bytes(b"broken")
    first = run_migration(tmp_path, "mem://vol")
    assert not first.ok and first.migrated == len(_ARCHIVES) - 1

    # Repair the source and resume: the already-verified ones are skipped.
    (tmp_path / archive_store.ARCHIVES_SUBDIR / victim).write_bytes(good)
    second = run_migration(tmp_path, "mem://vol", resume=True)
    assert second.ok and second.flipped
    assert second.skipped == len(_ARCHIVES) - 1
    assert second.migrated == 1


def test_doctor_healthy_after_migration(tmp_path, mem_backend):
    _populate(tmp_path, _ARCHIVES)
    run_migration(tmp_path, "mem://vol")
    report = doc.diagnose(tmp_path, deep=True)
    assert report.healthy
    assert report.active_uri == "mem://vol"
    assert report.findings == []


def test_doctor_detects_missing_and_corrupt(tmp_path, mem_backend):
    _populate(tmp_path, _ARCHIVES)
    run_migration(tmp_path, "mem://vol")

    missing_fn = "alpha-1.0.0-linux-x86_64-release-shared.tar.zst"
    corrupt_fn = "beta-1.0.0-linux-x86_64-release-shared.tar.zst"
    del mem_backend.objects[f"mem://vol/archives/{missing_fn}"]
    mem_backend.objects[f"mem://vol/archives/{corrupt_fn}"] = b"corrupted!"

    report = doc.diagnose(tmp_path, deep=True)
    assert not report.healthy
    assert {f.filename for f in report.missing} == {missing_fn}
    assert {f.filename for f in report.corrupt} == {corrupt_fn}


def test_doctor_heal_restores_from_source(tmp_path, mem_backend):
    _populate(tmp_path, _ARCHIVES)
    source_uri = f"file://{tmp_path}"  # the pre-migration backend still on disk
    run_migration(tmp_path, "mem://vol")

    # Botch the destination: delete one object, corrupt another.
    missing_fn = "alpha-1.0.0-linux-x86_64-release-shared.tar.zst"
    corrupt_fn = "gamma-1.0.0-linux-x86_64-release-shared.tar.zst"
    del mem_backend.objects[f"mem://vol/archives/{missing_fn}"]
    mem_backend.objects[f"mem://vol/archives/{corrupt_fn}"] = b"xx"

    heal = doc.heal(tmp_path, source_uri=source_uri)
    assert heal.ok
    assert set(heal.healed) == {missing_fn, corrupt_fn}
    assert heal.unhealable == []

    # Re-diagnose: fully healthy, bytes restored correctly.
    assert doc.diagnose(tmp_path, deep=True).healthy
    assert mem_backend.objects[f"mem://vol/archives/{missing_fn}"] == _ARCHIVES[missing_fn]


def test_doctor_heal_unhealable_when_source_also_missing(tmp_path, mem_backend):
    _populate(tmp_path, _ARCHIVES)
    run_migration(tmp_path, "mem://vol")

    victim = "beta-1.0.0-linux-x86_64-release-shared.tar.zst"
    del mem_backend.objects[f"mem://vol/archives/{victim}"]
    # Remove it from the source too, so heal cannot restore it.
    (tmp_path / archive_store.ARCHIVES_SUBDIR / victim).unlink()

    heal = doc.heal(tmp_path, source_uri=f"file://{tmp_path}")
    assert not heal.ok
    assert [fn for fn, _ in heal.unhealable] == [victim]


def test_doctor_heal_without_source_reports_unhealable(tmp_path, mem_backend):
    _populate(tmp_path, _ARCHIVES)
    run_migration(tmp_path, "mem://vol")
    victim = "beta-1.0.0-linux-x86_64-release-shared.tar.zst"
    del mem_backend.objects[f"mem://vol/archives/{victim}"]

    heal = doc.heal(tmp_path, source_uri="")  # no source given
    assert not heal.ok
    assert heal.unhealable and heal.unhealable[0][0] == victim


def test_migrate_db_catalog_mode(tmp_path, mem_backend):
    """Migration enumerates the catalog from a SQL DB, not just the YAML index.

    Production servers use the DB catalog, so exercise that code path: seed a
    SQLite catalog + on-disk archives, then migrate and verify against mem://.
    """
    import asyncio

    pytest.importorskip("aiosqlite")
    from cvcpkg.server.db import create_tables, dispose_engine, init_db
    from cvcpkg.server.db_stores import DbPackageIndex

    adir = tmp_path / archive_store.ARCHIVES_SUBDIR
    adir.mkdir(parents=True)
    for fn, data in _ARCHIVES.items():
        (adir / fn).write_bytes(data)

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'catalog.db'}"

    async def _seed():
        init_db(db_url)
        await create_tables()
        idx = DbPackageIndex()
        for fn, data in _ARCHIVES.items():
            await idx.add_package(
                name=fn.split("-")[0],
                version="1.0.0",
                platform="linux",
                arch="x86_64",
                build_type="release",
                link="shared",
                sha256=_sha(data),
                size_bytes=len(data),
                archive_url=f"/v1/download/{fn}",
            )
        await dispose_engine()

    asyncio.run(_seed())

    # Enumeration reads the DB catalog.
    refs = {r.filename: r for r in iter_catalog_archives(tmp_path, database_url=db_url)}
    assert set(refs) == set(_ARCHIVES)
    assert all(refs[fn].sha256 == _sha(data) for fn, data in _ARCHIVES.items())

    # And a full migration drives off it.
    result = run_migration(tmp_path, "mem://vol", database_url=db_url, deep_verify=True)
    assert result.ok and result.migrated == len(_ARCHIVES)
    for fn, data in _ARCHIVES.items():
        assert mem_backend.objects[f"mem://vol/archives/{fn}"] == data
