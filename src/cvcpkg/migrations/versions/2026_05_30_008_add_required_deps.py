"""add required_deps column to packages table

Revision ID: 008
Revises: 007
Create Date: 2026-05-30

Stores the JSON-encoded list of runtime dependencies for each published
package so the catalog can include them for client-side resolution.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "008"
down_revision: str | None = "007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_exists(table: str, column: str) -> bool:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = [c["name"] for c in insp.get_columns(table)]
    return column in cols


def upgrade() -> None:
    if not _column_exists("packages", "required_deps"):
        op.add_column(
            "packages",
            sa.Column("required_deps", sa.Text(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    if _column_exists("packages", "required_deps"):
        op.drop_column("packages", "required_deps")
