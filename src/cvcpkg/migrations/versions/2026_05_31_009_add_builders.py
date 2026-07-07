"""Add builders table for remote build agents.

Revision ID: 009
Revises: 008
Create Date: 2026-05-31

Adds the ``builders`` table for tracking registered build agents
that connect to the server to execute build jobs.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _table_exists(name: str) -> bool:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    return name in insp.get_table_names()


def upgrade() -> None:
    if not _table_exists("builders"):
        op.create_table(
            "builders",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("org_slug", sa.String(255), nullable=False, server_default=""),
            sa.Column("platform", sa.String(64), nullable=False),
            sa.Column("arch", sa.String(64), nullable=False),
            sa.Column("labels", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("capabilities", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(32), nullable=False, server_default="offline"),
            sa.Column("current_jobs", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_jobs", sa.Integer(), nullable=False, server_default="1"),
            sa.Column(
                "prefer_affinity",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column(
                "last_heartbeat",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
            sa.Column("registered_by", sa.String(255), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("name", "org_slug", name="uq_builder_name_org"),
        )
        op.create_index("ix_builders_org_slug", "builders", ["org_slug"])
        op.create_index("ix_builders_platform_arch", "builders", ["platform", "arch"])
        op.create_index("ix_builders_status", "builders", ["status"])


def downgrade() -> None:
    op.drop_index("ix_builders_status", table_name="builders")
    op.drop_index("ix_builders_platform_arch", table_name="builders")
    op.drop_index("ix_builders_org_slug", table_name="builders")
    op.drop_table("builders")
