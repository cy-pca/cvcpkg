"""add email column to tokens and token_requests table

Revision ID: 006
Revises: 005
Create Date: 2026-05-25

Adds ``email`` column to the ``tokens`` table and creates the
``token_requests`` table for admin-gated registration workflow.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: str | None = "005"
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
    # --- add email to tokens ---
    if _table_exists("tokens") and not _column_exists("tokens", "email"):
        op.add_column(
            "tokens",
            sa.Column(
                "email",
                sa.String(255),
                nullable=False,
                server_default="",
            ),
        )

    # --- token_requests ---
    if not _table_exists("token_requests"):
        op.create_table(
            "token_requests",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("email", sa.String(255), nullable=False, server_default=""),
            sa.Column("role", sa.String(32), nullable=False, server_default="reader"),
            sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
            sa.Column("reviewed_by", sa.String(255), nullable=False, server_default=""),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_token_requests_status", "token_requests", ["status"])


def downgrade() -> None:
    op.drop_table("token_requests")
    op.drop_column("tokens", "email")
