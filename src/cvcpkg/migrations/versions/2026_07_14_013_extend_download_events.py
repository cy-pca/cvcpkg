# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""extend download_events with analytics fields

Revision ID: 013
Revises: 012
Create Date: 2026-07-14

Adds the Phase 2 analytics columns to ``download_events``:
``arch``, ``client_ip_hash`` (salted SHA-256 — never the plain IP),
``user_agent``, ``cvcpkg_version``, and ``bytes_sent`` for bandwidth
accounting.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: str | None = "012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_COLUMNS = (
    ("arch", sa.String(64)),
    ("client_ip_hash", sa.String(64)),
    ("user_agent", sa.String(255)),
    ("cvcpkg_version", sa.String(64)),
)


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "download_events" not in insp.get_table_names():
        # Fresh databases get the full table from create_tables(); nothing
        # to migrate.
        return
    existing = {c["name"] for c in insp.get_columns("download_events")}
    for name, coltype in _NEW_COLUMNS:
        if name not in existing:
            op.add_column(
                "download_events",
                sa.Column(name, coltype, nullable=False, server_default=""),
            )
    if "bytes_sent" not in existing:
        op.add_column(
            "download_events",
            sa.Column("bytes_sent", sa.BigInteger(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "download_events" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("download_events")}
    for name in ("arch", "client_ip_hash", "user_agent", "cvcpkg_version", "bytes_sent"):
        if name in existing:
            op.drop_column("download_events", name)
