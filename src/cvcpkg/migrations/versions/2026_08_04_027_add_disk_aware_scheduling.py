# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""add build_jobs.min_disk_gb + builders.free_disk_gb (disk-aware scheduling)

Revision ID: 027
Revises: 026
Create Date: 2026-08-04

The scheduler had no notion of disk: it would hand a 35 GiB job (haiku-image
builds a Haiku cross-toolchain and a bootable image on the work volume) to a
builder with 26 GiB free, and the failure arrived only after the build had
already run for the better part of an hour.  These two columns make free disk
a routing constraint the same way ``required_capabilities`` (025) made the
CUDA toolchain one: the recipe declares ``build.min_disk_gb``, the builder
advertises what its work volume actually has, and the scheduler refuses to
pair them.

Both columns are NULLABLE, and NULL means *unknown* rather than zero.  A
builder that predates this migration advertises nothing; reading that as
"0 GiB free" would make it ineligible for every disk-bearing job forever, so
an unknown builder stays eligible and the recipe's own preflight catches the
mismatch.  ``build_jobs.min_disk_gb`` is NULL for the overwhelming majority
of jobs, which have no requirement at all.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "027"
down_revision: str | None = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())
    if "build_jobs" in tables:
        cols = {c["name"] for c in insp.get_columns("build_jobs")}
        if "min_disk_gb" not in cols:
            op.add_column("build_jobs", sa.Column("min_disk_gb", sa.Integer(), nullable=True))
    if "builders" in tables:
        cols = {c["name"] for c in insp.get_columns("builders")}
        if "free_disk_gb" not in cols:
            op.add_column("builders", sa.Column("free_disk_gb", sa.Integer(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())
    if "builders" in tables:
        cols = {c["name"] for c in insp.get_columns("builders")}
        if "free_disk_gb" in cols:
            op.drop_column("builders", "free_disk_gb")
    if "build_jobs" in tables:
        cols = {c["name"] for c in insp.get_columns("build_jobs")}
        if "min_disk_gb" in cols:
            op.drop_column("build_jobs", "min_disk_gb")
