"""add package metadata columns, organizations, and org_members tables

Revision ID: 002
Revises: 001
Create Date: 2026-05-25

Adds per-package metadata fields (description, homepage, license,
maintainer, tags), the org_slug column on packages, and the
organizations + org_members tables for organization support.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # -- Package metadata columns ------------------------------------------
    op.add_column(
        "packages",
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "packages",
        sa.Column("homepage", sa.String(512), nullable=False, server_default=""),
    )
    op.add_column(
        "packages",
        sa.Column("license", sa.String(128), nullable=False, server_default=""),
    )
    op.add_column(
        "packages",
        sa.Column("maintainer", sa.String(255), nullable=False, server_default=""),
    )
    op.add_column(
        "packages",
        sa.Column("tags", sa.Text(), nullable=False, server_default=""),
    )

    # -- Organization slug on packages -------------------------------------
    op.add_column(
        "packages",
        sa.Column("org_slug", sa.String(64), nullable=False, server_default=""),
    )
    op.create_index("ix_packages_org_slug", "packages", ["org_slug"])

    # -- Organizations table -----------------------------------------------
    op.create_table(
        "organizations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("logo_url", sa.String(512), nullable=False, server_default=""),
        sa.Column("homepage", sa.String(512), nullable=False, server_default=""),
        sa.Column(
            "storage_limit_bytes",
            sa.BigInteger(),
            nullable=False,
            server_default=str(10 * 1024 * 1024 * 1024),
        ),
        sa.Column("storage_used_bytes", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("created_by", sa.String(255), nullable=False, server_default=""),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"])

    # -- Organization members table ----------------------------------------
    op.create_table(
        "org_members",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_name", sa.String(255), nullable=False),
        sa.Column("role", sa.String(32), nullable=False, server_default="member"),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "token_name", name="uq_org_member"),
    )
    op.create_index("ix_org_members_org_id", "org_members", ["org_id"])
    op.create_index("ix_org_members_token_name", "org_members", ["token_name"])


def downgrade() -> None:
    op.drop_table("org_members")
    op.drop_table("organizations")

    op.drop_index("ix_packages_org_slug", table_name="packages")
    op.drop_column("packages", "org_slug")
    op.drop_column("packages", "tags")
    op.drop_column("packages", "maintainer")
    op.drop_column("packages", "license")
    op.drop_column("packages", "homepage")
    op.drop_column("packages", "description")
