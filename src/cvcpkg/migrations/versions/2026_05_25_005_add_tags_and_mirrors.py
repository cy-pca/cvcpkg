# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""add tags and mirrors tables

Revision ID: 005
Revises: 004
Create Date: 2026-05-25

Creates ``tags`` (curated tag metadata for the browse-by-tag front page)
and ``mirrors`` (registered mirror servers tracked by the primary) tables.
Checks for existing tables first so the migration is safe if create_tables()
already ran.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return name in insp.get_table_names()


def upgrade() -> None:
    # --- tags ---
    if not _table_exists("tags"):
        op.create_table(
            "tags",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(128), nullable=False),
            sa.Column("org_slug", sa.String(64), nullable=False, server_default=""),
            sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("description", sa.Text(), nullable=False, server_default=""),
            sa.Column("logo_url", sa.String(512), nullable=False, server_default=""),
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
            sa.Column("created_by", sa.String(255), nullable=False, server_default=""),
            sa.UniqueConstraint("name", "org_slug", name="uq_tag_name_org"),
        )
        op.create_index("ix_tags_name", "tags", ["name"])
        op.create_index("ix_tags_org_slug", "tags", ["org_slug"])

    # --- mirrors ---
    if not _table_exists("mirrors"):
        op.create_table(
            "mirrors",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("url", sa.String(2048), nullable=False, unique=True),
            sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("contact", sa.String(255), nullable=False, server_default=""),
            sa.Column(
                "registered_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("last_health_check", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_healthy_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("healthy", sa.Boolean(), nullable=False, server_default=sa.text("true")),
            sa.Column(
                "consecutive_failures",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
            sa.Column("rejected", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("rejected_by", sa.String(255), nullable=False, server_default=""),
            sa.Column("packages_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )
        op.create_index("ix_mirrors_url", "mirrors", ["url"])


def downgrade() -> None:
    op.drop_index("ix_mirrors_url", table_name="mirrors")
    op.drop_table("mirrors")
    op.drop_index("ix_tags_org_slug", table_name="tags")
    op.drop_index("ix_tags_name", table_name="tags")
    op.drop_table("tags")
