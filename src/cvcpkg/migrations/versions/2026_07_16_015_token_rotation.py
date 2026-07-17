"""token rotation grace-window columns

Revision ID: 015
Revises: 014
Create Date: 2026-07-16

Adds ``previous_token_hash`` and ``previous_hash_expires_at`` to
``tokens`` so a rotated token's old secret can keep verifying until the
grace window closes (POST /v1/tokens/{name}/rotate).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "015"
down_revision: str | None = "014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_COLUMNS = (
    ("previous_token_hash", sa.String(64)),
    ("previous_hash_expires_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "tokens" not in insp.get_table_names():
        # Fresh databases get the full table from create_tables(); nothing
        # to migrate.
        return
    existing = {c["name"] for c in insp.get_columns("tokens")}
    for name, coltype in _NEW_COLUMNS:
        if name not in existing:
            op.add_column("tokens", sa.Column(name, coltype, nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "tokens" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("tokens")}
    for name, _coltype in _NEW_COLUMNS:
        if name in existing:
            op.drop_column("tokens", name)
