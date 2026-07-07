"""Add webhooks table for event notification system.

Revision ID: 012
Revises: 011
Create Date: 2026-05-31

Adds the ``webhooks`` table for registering webhook endpoints
that receive notifications on package and build events.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "012"
down_revision: str | None = "011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return name in insp.get_table_names()


def upgrade() -> None:
    if not _table_exists("webhooks"):
        op.create_table(
            "webhooks",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("url", sa.String(2048), nullable=False),
            sa.Column("events", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("org_slug", sa.String(255), nullable=False, server_default=""),
            sa.Column("secret", sa.String(255), nullable=False),
            sa.Column(
                "active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
            sa.Column("registered_by", sa.String(255), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "last_delivery_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column(
                "consecutive_failures",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
        op.create_index("ix_webhooks_org_slug", "webhooks", ["org_slug"])
        op.create_index("ix_webhooks_active", "webhooks", ["active"])


def downgrade() -> None:
    op.drop_table("webhooks")
