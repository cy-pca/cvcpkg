# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""initial schema — packages, tokens, audit_log

Revision ID: 001
Revises:
Create Date: 2026-05-25

Matches the ORM models defined in cvcpkg.server.db as of v1.3.0.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "packages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(255), nullable=False),
        sa.Column("platform", sa.String(64), nullable=False, server_default=""),
        sa.Column("arch", sa.String(64), nullable=False, server_default=""),
        sa.Column("build_type", sa.String(32), nullable=False, server_default="release"),
        sa.Column("link", sa.String(32), nullable=False, server_default="shared"),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("archive_url", sa.Text(), nullable=False),
        sa.Column(
            "published_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("yanked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("signature", sa.Text(), nullable=False, server_default=""),
        sa.Column("key_fingerprint", sa.String(128), nullable=False, server_default=""),
        sa.Column("release_tag", sa.String(64), nullable=False, server_default=""),
        sa.Column("recipe_version", sa.String(128), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_packages_name", "packages", ["name"])
    op.create_index("ix_packages_release_tag", "packages", ["release_tag"])
    op.create_index(
        "ix_packages_unique_variant",
        "packages",
        ["name", "version", "platform", "arch", "build_type", "link"],
        unique=True,
    )

    op.create_table(
        "tokens",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
        sa.UniqueConstraint("token_hash"),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("actor", sa.String(255), nullable=False),
        sa.Column("target", sa.String(512), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False, server_default=""),
        sa.Column("prev_sha256", sa.String(64), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("tokens")
    op.drop_table("packages")
