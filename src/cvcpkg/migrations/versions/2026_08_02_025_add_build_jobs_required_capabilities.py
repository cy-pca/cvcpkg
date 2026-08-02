# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""add build_jobs.required_capabilities (build-time capability routing)

Revision ID: 025
Revises: 024
Create Date: 2026-08-02

A recipe that declares ``requires_capabilities: [cuda]`` (e.g. libcvc-cuda)
must build on a host with the matching toolchain — never on a CPU-only
builder, where it would fail on the missing nvcc.  This column carries the
recipe's requirement onto its build jobs so the scheduler can route them
only to builders advertising the capability (the build-side twin of the
install-side capability gating in the resolver).  JSON-encoded list of
capability names; '[]' for the overwhelming majority of jobs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "025"
down_revision: str | None = "024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "build_jobs" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("build_jobs")}
    if "required_capabilities" in cols:
        return
    op.add_column(
        "build_jobs",
        sa.Column("required_capabilities", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "build_jobs" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("build_jobs")}
    if "required_capabilities" in cols:
        op.drop_column("build_jobs", "required_capabilities")
