# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""packages.yanked_at

Revision ID: 018
Revises: 017
Create Date: 2026-07-17

Adds ``yanked_at`` to ``packages``.  ``yanked`` is a bare boolean, so there was
no way to tell how long a bundle had been yanked and therefore no way to expire
one; the yank-retention GC (CVCPKG_YANK_RETENTION_DAYS) keys on this column.

Deliberately NOT backfilled.  Rows yanked before this migration keep
``yanked_at IS NULL``, and the GC treats NULL as "never purge".  Stamping
``now()`` here would instead arm a deletion timer on historical data nobody
consented to lose, and it would fire a year after deploy -- far from any
context that explains it.  NULL-exempt fails in the recoverable direction: an
old yanked row lingers, visible and reclaimable via an explicit ``cvcpkg nuke``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "018"
down_revision: str | None = "017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        # Fresh databases get the full table from create_tables(); nothing
        # to migrate.
        return
    existing = {c["name"] for c in insp.get_columns("packages")}
    if "yanked_at" not in existing:
        op.add_column(
            "packages",
            sa.Column("yanked_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("packages")}
    if "yanked_at" in existing:
        op.drop_column("packages", "yanked_at")
