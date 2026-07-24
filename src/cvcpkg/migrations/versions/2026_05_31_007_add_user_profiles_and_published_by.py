# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""add description/metadata to tokens and published_by to packages

Revision ID: 007
Revises: 006
Create Date: 2026-05-31

Adds ``description`` and ``metadata`` columns to the ``tokens`` table
for user profiles, and ``published_by`` to the ``packages`` table to
track which user published each package.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return name in insp.get_table_names()


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    columns = [c["name"] for c in insp.get_columns(table)]
    return column in columns


def upgrade() -> None:
    # --- tokens: add description ---
    if _table_exists("tokens") and not _column_exists("tokens", "description"):
        op.add_column(
            "tokens",
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
        )

    # --- tokens: add metadata ---
    if _table_exists("tokens") and not _column_exists("tokens", "user_metadata"):
        op.add_column(
            "tokens",
            sa.Column("user_metadata", sa.Text(), nullable=False, server_default=""),
        )

    # --- packages: add published_by ---
    if _table_exists("packages") and not _column_exists("packages", "published_by"):
        op.add_column(
            "packages",
            sa.Column(
                "published_by",
                sa.String(255),
                nullable=False,
                server_default="",
            ),
        )


def downgrade() -> None:
    if _table_exists("packages") and _column_exists("packages", "published_by"):
        op.drop_column("packages", "published_by")
    if _table_exists("tokens") and _column_exists("tokens", "user_metadata"):
        op.drop_column("tokens", "user_metadata")
    if _table_exists("tokens") and _column_exists("tokens", "description"):
        op.drop_column("tokens", "description")
