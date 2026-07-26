# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""packages.diverges_upstream

Revision ID: 024
Revises: 023
Create Date: 2026-07-26

Records that a locally-published public bundle diverges from the populate
upstream: its coordinates (name/version/platform/arch/build_type/link) exist on
the upstream with a *different* sha256, so the local build shadows — and
disagrees with — the canonical upstream package.

Set and cleared by the populate sync (``reconcile_public_divergence``); the SPA
shows a warning symbol on flagged bundles and admins resolve it by nuking the
local bundle so upstream re-populates.

Defaults to False, so existing rows read as "agrees with upstream / not a
shadow" until a sync actually observes the disagreement.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "024"
down_revision: str | None = "023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        # Fresh databases get the full table from create_tables().
        return
    existing = {c["name"] for c in insp.get_columns("packages")}
    if "diverges_upstream" not in existing:
        op.add_column(
            "packages",
            sa.Column(
                "diverges_upstream",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("packages")}
    if "diverges_upstream" in existing:
        op.drop_column("packages", "diverges_upstream")
