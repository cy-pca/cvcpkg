"""add download_events table for analytics

Revision ID: 004
Revises: 003
Create Date: 2026-05-25

Creates a ``download_events`` table to track individual package
download events so the UI can render download-over-time charts.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "download_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("package_name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(255), nullable=False),
        sa.Column("platform", sa.String(64), nullable=False, server_default=""),
        sa.Column(
            "downloaded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_download_events_package_name", "download_events", ["package_name"])
    op.create_index("ix_download_events_downloaded_at", "download_events", ["downloaded_at"])
    op.create_index(
        "ix_download_events_name_date",
        "download_events",
        ["package_name", "downloaded_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_download_events_name_date", table_name="download_events")
    op.drop_index("ix_download_events_downloaded_at", table_name="download_events")
    op.drop_index("ix_download_events_package_name", table_name="download_events")
    op.drop_table("download_events")
