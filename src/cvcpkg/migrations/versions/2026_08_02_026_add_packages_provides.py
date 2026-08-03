# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""add packages.provides (virtual slot names)

Revision ID: 026
Revises: 025
Create Date: 2026-08-02

Every python package is a per-interpreter column recipe; the columns of a
package that ships console scripts declare ``provides: [<base>]`` (e.g.
every pytest-cpNNN column provides ``pytest``).  Providers of one slot are
mutually exclusive per prefix, and the client resolver matches an install
request for the slot name against its providers — which is what keeps
``cvcpkg install pytest`` working once the retired bare-name packages are
yanked.  Until now the slot list died at the publish boundary: the recipe
and the packed manifest carried it, but the server dropped it and the
catalog never served it.  JSON-encoded list of names; '[]' for the
overwhelming majority of packages.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "026"
down_revision: str | None = "025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("packages")}
    if "provides" in cols:
        return
    op.add_column(
        "packages",
        sa.Column("provides", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("packages")}
    if "provides" in cols:
        op.drop_column("packages", "provides")
