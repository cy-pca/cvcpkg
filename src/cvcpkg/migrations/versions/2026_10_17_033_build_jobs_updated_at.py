# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""build_jobs.updated_at

Revision ID: 033
Revises: 032
Create Date: 2026-10-17

The builds page and the build detail page update live over ``/v1/live/ws``.
To push what changed, the server asks for jobs updated since its last look;
``updated_at`` (bumped by every write to the row) makes that one indexed range
query.  Existing rows get the newest of their own timestamps, so finished jobs
do not all look freshly changed after the upgrade.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "033"
down_revision: str | None = "032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "build_jobs" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("build_jobs")}
    if "updated_at" not in cols:
        if conn.dialect.name == "sqlite":
            # SQLite cannot ADD COLUMN with a CURRENT_TIMESTAMP default; the
            # ORM sets the value on every insert and update anyway.
            column = sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True)
        else:
            column = sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            )
        op.add_column("build_jobs", column)
        conn.execute(
            sa.text(
                "UPDATE build_jobs SET updated_at = "
                "COALESCE(finished_at, started_at, submitted_at, updated_at)"
            )
        )
    indexes = {ix["name"] for ix in insp.get_indexes("build_jobs")}
    if "ix_build_jobs_updated_at" not in indexes:
        op.create_index("ix_build_jobs_updated_at", "build_jobs", ["updated_at"])


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "build_jobs" not in insp.get_table_names():
        return
    indexes = {ix["name"] for ix in insp.get_indexes("build_jobs")}
    if "ix_build_jobs_updated_at" in indexes:
        op.drop_index("ix_build_jobs_updated_at", table_name="build_jobs")
    cols = {c["name"] for c in insp.get_columns("build_jobs")}
    if "updated_at" in cols:
        op.drop_column("build_jobs", "updated_at")
