# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""build_jobs.dag_id prefix index

Revision ID: 035
Revises: 034
Create Date: 2026-10-19

``GET /v1/builds?dag_id=<prefix>*`` is how populate runs poll their DAGs, and it
filters with ``dag_id LIKE '<prefix>%'``.  ``ix_build_jobs_dag_id`` is a plain
btree, which postgres cannot use for LIKE under a non-C collation, so each poll
was two sequential scans of ``build_jobs`` (the count and the page): ~21 a
minute on cvcpkg.org on 2026-10-19, ~10 ms each over 21,822 rows.  A
``varchar_pattern_ops`` index serves the prefix as an index range.  On SQLite
this is an ordinary index of the same name, matching the model.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "035"
down_revision: str | None = "034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_build_jobs_dag_id_pattern"


def upgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "build_jobs" not in insp.get_table_names():
        return
    if _INDEX not in {ix["name"] for ix in insp.get_indexes("build_jobs")}:
        op.create_index(
            _INDEX,
            "build_jobs",
            ["dag_id"],
            postgresql_ops={"dag_id": "varchar_pattern_ops"},
        )


def downgrade() -> None:
    insp = sa.inspect(op.get_bind())
    if "build_jobs" not in insp.get_table_names():
        return
    if _INDEX in {ix["name"] for ix in insp.get_indexes("build_jobs")}:
        op.drop_index(_INDEX, table_name="build_jobs")
