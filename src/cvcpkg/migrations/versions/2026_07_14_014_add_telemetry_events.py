# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""add telemetry_events table (opt-in client telemetry)

Revision ID: 014
Revises: 013
Create Date: 2026-07-14

Phase 2 roadmap: strictly anonymous, opt-in client environment pings.
No address (not even hashed), no hostname, no user, no paths.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "014"
down_revision: str | None = "013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "telemetry_events" in insp.get_table_names():
        return
    op.create_table(
        "telemetry_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("platform", sa.String(64), nullable=False, server_default=""),
        sa.Column("arch", sa.String(64), nullable=False, server_default=""),
        sa.Column("python_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("cvcpkg_version", sa.String(64), nullable=False, server_default=""),
        sa.Column("ci", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tools", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_telemetry_events_received_at", "telemetry_events", ["received_at"])


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "telemetry_events" in insp.get_table_names():
        op.drop_index("ix_telemetry_events_received_at", table_name="telemetry_events")
        op.drop_table("telemetry_events")
