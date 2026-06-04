"""Add recipes table for server-managed recipe distribution.

Revision ID: 011
Revises: 010
Create Date: 2026-05-31

Adds the ``recipes`` table for storing recipe bundles that can be
pushed to remote builders.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: str | None = "010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return name in insp.get_table_names()


def upgrade() -> None:
    if not _table_exists("recipes"):
        op.create_table(
            "recipes",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("version", sa.String(128), nullable=False, server_default=""),
            sa.Column("recipe_hash", sa.String(128), nullable=False, server_default=""),
            sa.Column("org_slug", sa.String(255), nullable=False, server_default=""),
            sa.Column("bundle_path", sa.String(1024), nullable=False),
            sa.Column("bundle_size", sa.BigInteger(), nullable=False, server_default="0"),
            sa.Column("uploaded_by", sa.String(255), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_unique_constraint("uq_recipe_name_org", "recipes", ["name", "org_slug"])
        op.create_index("ix_recipes_name", "recipes", ["name"])
        op.create_index("ix_recipes_org_slug", "recipes", ["org_slug"])


def downgrade() -> None:
    op.drop_table("recipes")
