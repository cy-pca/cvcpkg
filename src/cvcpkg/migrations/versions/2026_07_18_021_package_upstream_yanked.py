"""packages.upstream_yanked

Revision ID: 021
Revises: 020
Create Date: 2026-07-18

Tracks upstream's yank verdict separately from the local one, so the two can
legitimately disagree.

Reconciliation enforces an upstream yank once.  If a mirror operator then
deliberately unyanks -- they need the bundle to unblock a build, or they know
something upstream does not -- that decision has to survive the next sync.  With
a single ``yanked`` flag there is nowhere to record "upstream says retired, we
have chosen otherwise", so every sync silently reverted the operator and the
bundle flip-flopped forever.

Defaults to False, which reads as "upstream has said nothing", so existing rows
are untouched until reconciliation observes an actual upstream verdict.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "021"
down_revision: str | None = "020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("packages")}
    if "upstream_yanked" not in existing:
        op.add_column(
            "packages",
            sa.Column(
                "upstream_yanked",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns("packages")}
    if "upstream_yanked" in existing:
        op.drop_column("packages", "upstream_yanked")
