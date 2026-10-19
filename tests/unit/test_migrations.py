"""Migration-chain integrity checks (v1.x → v2.0.0 schema history).

Most of these run without a database: they verify the Alembic revision graph
is a single, unbroken, cycle-free line from the initial v1 schema to the
current head, and that every ORM model table is created by some migration
(catching "added a model but forgot the migration" drift).  Running the
migrations against a live PostgreSQL is exercised by the Docker integration
job.

``test_alembic_upgrade_head_on_sqlite`` additionally drives the real chain
against a throwaway SQLite file.  The rest of the suite gets its schema from
create_tables()/metadata.create_all, which never calls Alembic at all — so
without this test a migration can be unrunnable on SQLite indefinitely while
every other test stays green.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("alembic", reason="alembic required for migration checks")

_ROOT = Path(__file__).resolve().parents[2]
_VERSIONS = _ROOT / "src" / "cvcpkg" / "migrations" / "versions"


def _load_revision_records() -> list[tuple[str, str | None, str]]:
    """Return (revision, down_revision, filename) for every migration module."""
    records: list[tuple[str, str | None, str]] = []
    for f in sorted(_VERSIONS.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"_mig_{f.stem}", f)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        records.append((mod.revision, mod.down_revision, f.name))
    return records


def _load_revisions() -> dict[str, str | None]:
    """Return {revision: down_revision} for every migration module.

    Keyed by revision, so this cannot represent a collision — two files
    claiming one revision collapse to a single entry.  test_no_duplicate_
    revision_ids is what rules that out; this mapping assumes it.
    """
    return {rev: down for rev, down, _ in _load_revision_records()}


def test_no_duplicate_revision_ids():
    """No two migrations may claim the same revision.

    A collision gives alembic two heads and breaks `alembic upgrade head` on
    every backend, PostgreSQL included.  It is invisible to the head count in
    test_single_head_linear_chain, whose dict keeps only the last file with a
    given revision — which is how two 015s reached master on 2026-07-16.
    """
    by_rev: dict[str, list[str]] = {}
    for rev, _down, name in _load_revision_records():
        by_rev.setdefault(rev, []).append(name)

    dupes = {rev: sorted(files) for rev, files in by_rev.items() if len(files) > 1}
    assert not dupes, f"revision id claimed by more than one migration: {dupes}"


def test_single_head_linear_chain():
    revs = _load_revisions()
    assert len(revs) >= 12

    downs = {d for d in revs.values() if d is not None}
    heads = set(revs) - downs
    assert len(heads) == 1, f"expected exactly one head, got {sorted(heads)}"

    bases = [r for r, d in revs.items() if d is None]
    assert len(bases) == 1, f"expected exactly one base, got {bases}"

    for rev, down in revs.items():
        assert down is None or down in revs, f"{rev} points to unknown revision {down!r}"

    # Walk head → base: no cycles, and every revision is on the chain.
    seen: set[str] = set()
    cur: str | None = next(iter(heads))
    while cur is not None:
        assert cur not in seen, f"cycle detected at {cur}"
        seen.add(cur)
        cur = revs[cur]
    assert seen == set(revs), f"revisions off the main line: {set(revs) - seen}"


def test_every_model_table_has_a_create_table_migration():
    from cvcpkg.server.db import Base

    created: set[str] = set()
    # create_table( is followed by the table name on the next line.
    pat = re.compile(r"create_table\(\s*[\"']([a-z_]+)[\"']")
    for f in _VERSIONS.glob("*.py"):
        created |= set(pat.findall(f.read_text()))

    missing = set(Base.metadata.tables) - created
    assert not missing, f"model tables with no create_table migration: {sorted(missing)}"


def _alembic_upgrade(db: Path, revision: str) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "CVCPKG_DATABASE_URL": f"sqlite+aiosqlite:///{db.as_posix()}",
        "PYTHONPATH": str(_ROOT / "src"),
    }
    return subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def test_migration_023_backfills_served_namespaces(tmp_path):
    """A builder that existed before migration 023 keeps working: the upgrade
    backfills served_namespaces = [org_slug], preserving 1:1 scheduling until
    the builder re-registers with a wider set."""
    pytest.importorskip("aiosqlite", reason="aiosqlite required to migrate SQLite")
    db = tmp_path / "backfill.db"

    # Bring the schema to just before this migration.
    p = _alembic_upgrade(db, "022")
    assert p.returncode == 0, p.stderr

    # Insert a builder under the OLD schema (no served_namespaces column yet).
    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO builders "
        "(name, org_slug, platform, arch, labels, capabilities, status, "
        " current_jobs, max_jobs, prefer_affinity, registered_by) "
        "VALUES ('b1', 'cvc', 'linux', 'x86_64', '[]', '{}', 'online', 0, 1, 0, 't')"
    )
    con.commit()
    con.close()

    # Apply migration 023.
    p = _alembic_upgrade(db, "023")
    assert p.returncode == 0, p.stderr

    con = sqlite3.connect(db)
    (served,) = con.execute("SELECT served_namespaces FROM builders WHERE name = 'b1'").fetchone()
    con.close()
    assert served == '["cvc"]', served


def test_migration_032_backfills_package_events(tmp_path):
    """The activity feed has history on day one: migration 032 reconstructs
    publish, yank and nuke events from ``packages`` and ``package_tombstones``,
    oldest first, so ids follow time the way live events do."""
    pytest.importorskip("aiosqlite", reason="aiosqlite required to migrate SQLite")
    db = tmp_path / "events.db"
    p = _alembic_upgrade(db, "031")
    assert p.returncode == 0, p.stderr

    con = sqlite3.connect(db)
    con.execute(
        "INSERT INTO packages (name, version, platform, arch, sha256, archive_url, "
        " size_bytes, published_at, published_by, org_slug) "
        "VALUES ('zlib', '1.3', 'linux', 'x86_64', 'x', 'u', 7, "
        " '2026-01-01 00:00:00', 'ci', '')"
    )
    con.execute(
        "INSERT INTO packages (name, version, platform, arch, sha256, archive_url, "
        " published_at, published_by, org_slug, yanked, yanked_at) "
        "VALUES ('vtk', '9.3', 'macos', 'arm64', 'x', 'u', '2026-02-01 00:00:00', "
        " 'joe', 'acme', 1, '2026-03-01 00:00:00')"
    )
    con.execute(
        "INSERT INTO package_tombstones (name, version, platform, reason, nuked_by, "
        " nuked_at, published_at, yanked_at) "
        "VALUES ('old', '0.1', 'linux', 'retention', 'retention-gc', "
        " '2026-04-01 00:00:00', '2025-12-01 00:00:00', '2026-01-15 00:00:00')"
    )
    con.commit()
    con.close()

    p = _alembic_upgrade(db, "032")
    assert p.returncode == 0, p.stderr

    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT kind, name, org_slug, actor, size_bytes, detail FROM package_events ORDER BY id"
    ).fetchall()
    cols = {r[1] for r in con.execute("PRAGMA table_info(builders)")}
    con.close()
    assert rows == [
        ("publish", "old", "", "", 0, "backfilled"),
        ("publish", "zlib", "", "ci", 7, "backfilled"),
        ("yank", "old", "", "", 0, "backfilled"),
        ("publish", "vtk", "acme", "joe", 0, "backfilled"),
        ("yank", "vtk", "acme", "", 0, "backfilled"),
        ("nuke", "old", "", "retention-gc", 0, "backfilled"),
    ]
    assert "system_stats" in cols


@pytest.fixture(scope="module")
def _sqlite_upgrade_head(tmp_path_factory):
    """Run ``alembic upgrade head`` once against an empty SQLite file.

    Returns (CompletedProcess, db_path).  The database must be *fresh*: several
    migrations skip their body when the table already exists, so a reused file
    would mask exactly the breakage this is here to catch.
    """
    pytest.importorskip("aiosqlite", reason="aiosqlite required to migrate SQLite")

    db = tmp_path_factory.mktemp("alembic") / "migrate.db"
    # as_posix() keeps this a valid URL on Windows (C:/...) and POSIX (//tmp/...).
    env = {
        **os.environ,
        "CVCPKG_DATABASE_URL": f"sqlite+aiosqlite:///{db.as_posix()}",
        "PYTHONPATH": str(_ROOT / "src"),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_ROOT,  # alembic.ini lives at the repo root
        env=env,
        capture_output=True,
        text=True,
    )
    return proc, db


def test_alembic_upgrade_head_on_sqlite(_sqlite_upgrade_head):
    """The full chain applies to SQLite, not just PostgreSQL.

    SQLite is the documented dev / small-install backend, but it cannot ALTER
    a table to add a constraint.  A migration that does so outside
    op.batch_alter_table() raises NotImplementedError and leaves every
    SQLite deployment unable to migrate, while PostgreSQL CI stays green.
    """
    proc, _ = _sqlite_upgrade_head
    assert proc.returncode == 0, (
        "alembic upgrade head failed against SQLite\n"
        f"--- stdout ---\n{proc.stdout}\n--- stderr ---\n{proc.stderr}"
    )


def test_migration_029_columns_match_orm(_sqlite_upgrade_head):
    """Alembic-built auth schema is a superset of the ORM's — no silent drift.

    Every other test builds schema via metadata.create_all, never Alembic, so a
    migration that omitted or misnamed one of the sessions refresh columns or a
    new cli_* table would pass CI while an alembic-migrated Postgres deploy fails
    at runtime (e.g. verify_bearer selecting a nonexistent token_hash).
    """
    from cvcpkg.server.db import Base

    proc, db = _sqlite_upgrade_head
    assert proc.returncode == 0, "upgrade failed; see test_alembic_upgrade_head_on_sqlite"

    conn = sqlite3.connect(db)
    try:
        for table in (
            "package_events",
            "builders",
            "sessions",
            "cli_pairings",
            "cli_auth_codes",
            "cli_login_txns",
            "code_attempts",
        ):
            migrated = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert migrated, f"migration did not create table {table!r}"
            orm = set(Base.metadata.tables[table].columns.keys())
            missing = orm - migrated
            assert not missing, f"{table}: migration missing columns {sorted(missing)}"
    finally:
        conn.close()


def test_sqlite_chain_builds_the_recipe_unique_constraint(_sqlite_upgrade_head):
    """uq_recipe_name_org survives the SQLite batch-mode rebuild.

    Guards the tempting bad fix for the error above: dropping the constraint
    to make SQLite quiet.  That would keep the rc==0 test passing while
    letting duplicate (name, org_slug) recipes into the table.
    """
    proc, db = _sqlite_upgrade_head
    assert proc.returncode == 0, "upgrade failed; see test_alembic_upgrade_head_on_sqlite"

    conn = sqlite3.connect(db)
    try:
        row = ("dup", "acme", "/bundles/dup.tar.zst", "someone")
        conn.execute(
            "insert into recipes (name, org_slug, bundle_path, uploaded_by) values (?,?,?,?)",
            row,
        )
        with pytest.raises(sqlite3.IntegrityError, match="name, recipes.org_slug"):
            conn.execute(
                "insert into recipes (name, org_slug, bundle_path, uploaded_by) "
                "values (?,?,?,?)",
                row,
            )
        # batch mode copy-and-moves via a temp table; it must not leave one behind.
        leaked = conn.execute(
            "select name from sqlite_master where name like '_alembic_tmp%'"
        ).fetchall()
        assert not leaked, f"batch mode left temp tables behind: {leaked}"
    finally:
        conn.close()


def _build_job_deps_indexes(db: Path) -> set[str]:
    conn = sqlite3.connect(db)
    try:
        return {r[1] for r in conn.execute("PRAGMA index_list(build_job_deps)")}
    finally:
        conn.close()


_BUILD_JOB_DEPS_INDEXES = {"ix_build_job_deps_job_id", "ix_build_job_deps_depends_on"}


def test_sqlite_chain_builds_the_build_job_deps_indexes(_sqlite_upgrade_head):
    proc, db = _sqlite_upgrade_head
    assert proc.returncode == 0, "upgrade failed; see test_alembic_upgrade_head_on_sqlite"
    assert _BUILD_JOB_DEPS_INDEXES.issubset(_build_job_deps_indexes(db))


def test_migration_031_creates_missing_build_job_deps_indexes(tmp_path):
    """A build_job_deps without its indexes (cvcpkg.org's) gets them from 031.

    Migration 010 skipped both indexes wherever the table already existed, so
    the scheduler's dependents lookups scanned the whole table.
    """
    pytest.importorskip("aiosqlite", reason="aiosqlite required to migrate SQLite")
    db = tmp_path / "repair.db"
    env = {
        **os.environ,
        "CVCPKG_DATABASE_URL": f"sqlite+aiosqlite:///{db.as_posix()}",
        "PYTHONPATH": str(_ROOT / "src"),
    }

    def alembic(*args: str) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 0, f"alembic {' '.join(args)}\n{proc.stdout}\n{proc.stderr}"

    alembic("upgrade", "030")
    conn = sqlite3.connect(db)
    for name in _BUILD_JOB_DEPS_INDEXES:
        conn.execute(f"DROP INDEX {name}")
    conn.commit()
    conn.close()
    assert not _BUILD_JOB_DEPS_INDEXES & _build_job_deps_indexes(db)

    alembic("upgrade", "head")
    assert _BUILD_JOB_DEPS_INDEXES.issubset(_build_job_deps_indexes(db))
    # The unique constraint is untouched, and downgrading 031 keeps 010's indexes.
    assert any(n.startswith("sqlite_autoindex") for n in _build_job_deps_indexes(db))
    alembic("downgrade", "030")
    assert _BUILD_JOB_DEPS_INDEXES.issubset(_build_job_deps_indexes(db))


def test_lockfile_reads_legacy_v1_schema(tmp_path):
    """A pre-2.0 lockfile (schema_version 1, no config fields) still loads."""
    import yaml

    from cvcpkg.lockfile import Lockfile

    legacy = {
        "schema_version": 1,
        "platform": "linux",
        "arch": "x86_64",
        "bundles": [{"name": "zlib", "version": "1.3.1+cvc.1"}],
    }
    path = tmp_path / "lockfile.yaml"
    path.write_text(yaml.safe_dump(legacy))

    lock = Lockfile.read(path)
    assert lock.platform == "linux"
    assert lock.bundles[0].name == "zlib"
    assert lock.bundles[0].version == "1.3.1+cvc.1"


def test_migration_034_backfills_and_indexes_archive_name(tmp_path):
    """Download lookups match ``archive_name`` exactly instead of scanning
    ``archive_url LIKE '%/<file>'``; 034 fills it in for rows that predate it."""
    pytest.importorskip("aiosqlite", reason="aiosqlite required to migrate SQLite")
    db = tmp_path / "archive_name.db"
    p = _alembic_upgrade(db, "033")
    assert p.returncode == 0, p.stderr

    con = sqlite3.connect(db)
    for name, url in (
        ("zlib", "/v1/download/zlib-1.3+cvc.1-linux-x86_64-release-shared.tar.zst"),
        ("a_b", "https://mirror.example/v1/download/a_b-1.0-linux-x86_64-release-shared.tar.zst"),
        ("empty", ""),
    ):
        con.execute(
            "INSERT INTO packages (name, version, platform, arch, sha256, archive_url) "
            "VALUES (?, '1', 'linux', 'x86_64', 'x', ?)",
            (name, url),
        )
    con.commit()
    con.close()

    p = _alembic_upgrade(db, "034")
    assert p.returncode == 0, p.stderr

    con = sqlite3.connect(db)
    names = dict(con.execute("SELECT name, archive_name FROM packages").fetchall())
    indexes = {r[1] for r in con.execute("PRAGMA index_list(packages)")}
    con.close()
    assert names == {
        "zlib": "zlib-1.3+cvc.1-linux-x86_64-release-shared.tar.zst",
        "a_b": "a_b-1.0-linux-x86_64-release-shared.tar.zst",
        "empty": "",
    }
    assert "ix_packages_archive_name" in indexes
