# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""add packages.conflicts (packages a bundle may not share a prefix with)

Revision ID: 038
Revises: 037
Create Date: 2026-11-13

A recipe's ``conflicts:`` (e.g. ``mujoco`` and ``mujoco3``, which install the
same files) was enforced only by ``cvcpkg install``'s recipe lookup, which reads
the recipes bundled in the client.  A package newer than the client's recipes
got no check at all: on 2026-11-13, ``cvcpkg install mujoco`` into a prefix
holding ``mujoco3`` silently overwrote it with client 2.5.6.  The bundle
manifest now carries the list, publish records it here, and the catalog serves
it, so any client can refuse the pair (``provides`` slots, migration 026, were
already served).

JSON-encoded list of package names; '[]' for nearly every package.  Rows
published before this column existed read '[]' until the backfill-deps admin
endpoint fills them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "038"
down_revision: str | None = "037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("packages")}
    if "conflicts" in cols:
        return
    op.add_column(
        "packages",
        sa.Column("conflicts", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("packages")}
    if "conflicts" in cols:
        op.drop_column("packages", "conflicts")
