"""add is_private column to organizations table

Revision ID: 003
Revises: 002
Create Date: 2026-05-25

Adds a boolean ``is_private`` column so organization admins can hide
their org (and its packages) from public listings.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    columns = [c["name"] for c in insp.get_columns("organizations")]
    if "is_private" not in columns:
        op.add_column(
            "organizations",
            sa.Column("is_private", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )


def downgrade() -> None:
    op.drop_column("organizations", "is_private")
