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
