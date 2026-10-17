# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""create build_job_deps' indexes where migration 010 skipped them

Revision ID: 031
Revises: 030
Create Date: 2026-10-17

Migration 010 creates ``ix_build_job_deps_job_id`` and
``ix_build_job_deps_depends_on`` only on a database where it also creates the
``build_job_deps`` table.  Where the server's ``create_all`` made the table
first, 010 skipped both, and the ORM model did not declare them, so nothing
ever created them.  cvcpkg.org's table had only ``uq_build_job_dep``: on
2026-10-17 postgres counted 1.69M sequential scans of build_job_deps (54.6B
rows read), because every lookup of a job's dependents (the ready-job check,
``cancel_downstream``, and the ON DELETE CASCADE when old jobs are purged)
filters on ``depends_on_job_id``, which no index led with.

This creates whichever of the two is missing.  ``uq_build_job_dep`` already
leads with ``job_id``, so the first index mostly duplicates it on postgres;
it is created anyway so every database ends up with the schema a fresh
migration chain builds.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "031"
down_revision: str | None = "030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEXES = (
    ("ix_build_job_deps_job_id", ["job_id"]),
    ("ix_build_job_deps_depends_on", ["depends_on_job_id"]),
)


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "build_job_deps" not in insp.get_table_names():
        return
    existing = {ix["name"] for ix in insp.get_indexes("build_job_deps")}
    for name, columns in _INDEXES:
        if name not in existing:
            op.create_index(name, "build_job_deps", columns)


def downgrade() -> None:
    # Nothing to undo: the indexes are part of 010's schema, and 010's
    # downgrade drops them.  Dropping them here would leave 010's downgrade
    # failing on a database this migration repaired.
    pass
