"""add build_jobs.claimed_by (unregistered workers draining a queue)

Revision ID: 015
Revises: 014
Create Date: 2026-07-16

Platforms with no persistent builder (macOS: GitHub-hosted runners are
ephemeral) drain their queue without registering.  Such a job has a NULL
builder_id, so without this column a running job would be anonymous — you
could not tell which CI run was building it.  claimed_by carries that
identity (e.g. 'gha-run-29372085620').
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "build_jobs" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("build_jobs")}
    if "claimed_by" in cols:
        return
    op.add_column(
        "build_jobs",
        sa.Column("claimed_by", sa.String(255), nullable=False, server_default=""),
    )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "build_jobs" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("build_jobs")}
    if "claimed_by" in cols:
        op.drop_column("build_jobs", "claimed_by")
