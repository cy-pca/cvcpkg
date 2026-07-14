"""Migration-chain integrity checks (v1.x → v2.0.0 schema history).

These run without a database: they verify the Alembic revision graph is a
single, unbroken, cycle-free line from the initial v1 schema to the current
head, and that every ORM model table is created by some migration (catching
"added a model but forgot the migration" drift).  Running the migrations
themselves against a live PostgreSQL is exercised by the Docker integration
job; SQLite users get the schema via create_tables()/metadata.create_all.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

pytest.importorskip("alembic", reason="alembic required for migration checks")

_VERSIONS = Path(__file__).resolve().parents[2] / "src" / "cvcpkg" / "migrations" / "versions"


def _load_revisions() -> dict[str, str | None]:
    """Return {revision: down_revision} for every migration module."""
    revs: dict[str, str | None] = {}
    for f in sorted(_VERSIONS.glob("*.py")):
        spec = importlib.util.spec_from_file_location(f"_mig_{f.stem}", f)
        mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(mod)
        revs[mod.revision] = mod.down_revision
    return revs


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
