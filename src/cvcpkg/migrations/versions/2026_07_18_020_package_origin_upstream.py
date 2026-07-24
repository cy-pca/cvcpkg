# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""packages.origin_upstream

Revision ID: 020
Revises: 019
Create Date: 2026-07-18

Records which upstream a bundle was imported from, so a mirror/edge can follow
that upstream's yank and nuke decisions without ever touching a package it
published itself.

Deliberately NOT backfilled.  Rows that predate this column keep
``origin_upstream = ""``, which reconciliation reads as "locally published" and
therefore leaves alone.  Guessing the other way -- stamping every existing
public row with the configured upstream -- would hand reconciliation authority
over data whose provenance nobody actually recorded, and the first sync could
then yank an edge's own packages merely because the upstream never had them.
An un-backfilled row is re-stamped naturally the next time populate imports it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "020"
down_revision: str | None = "019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        # Fresh databases get the full table from create_tables().
        return
    existing = {c["name"] for c in insp.get_columns("packages")}
    if "origin_upstream" not in existing:
        op.add_column(
            "packages",
            sa.Column(
                "origin_upstream",
                sa.Text(),
                nullable=False,
                server_default="",
            ),
        )
        indexes = {i["name"] for i in insp.get_indexes("packages")}
        if "ix_packages_origin_upstream" not in indexes:
            op.create_index(
                "ix_packages_origin_upstream",
                "packages",
                ["origin_upstream"],
            )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    indexes = {i["name"] for i in insp.get_indexes("packages")}
    if "ix_packages_origin_upstream" in indexes:
        op.drop_index("ix_packages_origin_upstream", table_name="packages")
    existing = {c["name"] for c in insp.get_columns("packages")}
    if "origin_upstream" in existing:
        op.drop_column("packages", "origin_upstream")
