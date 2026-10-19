# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""packages.archive_name, indexed

Revision ID: 034
Revises: 033
Create Date: 2026-10-19

Every download resolves the file it serves back to its package row (org
visibility, analytics, the yank check behind a tombstone).  Those lookups were
``archive_url LIKE '%/<file>'``, which no index can serve, so each one was a
sequential scan of ``packages``: 2-3 per download, and on 2026-10-19 cvcpkg.org
ran 358 full scans of the table in two minutes at ~2 requests/s.

``archive_name`` holds the file name (``archive_url`` after its last "/") so the
lookup is an indexed equality.  The ORM keeps it in step with ``archive_url``;
this backfills the rows that already exist.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "034"
down_revision: str | None = "033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_packages_archive_name"


def _name_of(archive_url: str) -> str:
    # Same rule as cvcpkg.server.db.archive_name_of, copied so the migration
    # does not change if that helper ever does.
    return (archive_url or "").rstrip("/").rsplit("/", 1)[-1]


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("packages")}
    if "archive_name" not in cols:
        op.add_column(
            "packages",
            sa.Column("archive_name", sa.Text(), nullable=False, server_default=""),
        )
    packages = sa.table(
        "packages",
        sa.column("id", sa.Integer),
        sa.column("archive_url", sa.Text),
        sa.column("archive_name", sa.Text),
    )
    rows = conn.execute(
        sa.select(packages.c.id, packages.c.archive_url).where(packages.c.archive_name == "")
    ).all()
    for row_id, url in rows:
        name = _name_of(url)
        if name:
            conn.execute(packages.update().where(packages.c.id == row_id).values(archive_name=name))
    if _INDEX not in {ix["name"] for ix in insp.get_indexes("packages")}:
        op.create_index(_INDEX, "packages", ["archive_name"])


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    if _INDEX in {ix["name"] for ix in insp.get_indexes("packages")}:
        op.drop_index(_INDEX, table_name="packages")
    if "archive_name" in {c["name"] for c in insp.get_columns("packages")}:
        op.drop_column("packages", "archive_name")
