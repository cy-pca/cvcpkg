# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""package_events and builders.system_stats

Revision ID: 032
Revises: 031
Create Date: 2026-10-17

``package_events`` backs the activity feed: one row per publish, yank, unyank,
delete or nuke of one bundle variant, with the variant's org so the feed can
hide a private org's events from non-members.  The audit log could not serve
it: it is admin-only, keeps one entry per request, and names its target in
free text with no org.

The new table is filled from what the database already knows, so the feed has
history on the day it is deployed:

- a ``publish`` for every ``packages`` row, at ``published_at``, by
  ``published_by``;
- a ``yank`` for every row with a ``yanked_at`` (who yanked it was never
  recorded on the row, so the actor is empty);
- a ``publish``, ``yank`` and ``nuke`` for every ``package_tombstones`` row,
  from its ``published_at``, ``yanked_at`` and ``nuked_at``.

Backfilled rows carry ``detail = 'backfilled'``.  Unyanks and deletes before
this migration left no trace and are not reconstructed.

``builders.system_stats`` holds the CPU, memory and disk figures a builder
sends with each heartbeat (JSON, NULL until it sends one).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "032"
down_revision: str | None = "031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_VARIANT = "name, version, platform, arch, build_type, link, org_slug"

_BACKFILL = f"""
INSERT INTO package_events
    (created_at, kind, {_VARIANT}, actor, size_bytes, detail)
SELECT ts, kind, {_VARIANT}, actor, size_bytes, 'backfilled'
FROM (
    SELECT published_at AS ts, 'publish' AS kind, {_VARIANT},
           published_by AS actor, size_bytes
      FROM packages
    UNION ALL
    SELECT yanked_at, 'yank', {_VARIANT}, '', size_bytes
      FROM packages WHERE yanked_at IS NOT NULL
    UNION ALL
    SELECT published_at, 'publish', {_VARIANT}, '', size_bytes
      FROM package_tombstones WHERE published_at IS NOT NULL
    UNION ALL
    SELECT yanked_at, 'yank', {_VARIANT}, '', size_bytes
      FROM package_tombstones WHERE yanked_at IS NOT NULL
    UNION ALL
    SELECT nuked_at, 'nuke', {_VARIANT}, nuked_by, size_bytes
      FROM package_tombstones
) backfill
ORDER BY ts
"""


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())
    if "package_events" not in tables:
        op.create_table(
            "package_events",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("kind", sa.String(16), nullable=False),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("version", sa.String(255), nullable=False, server_default=""),
            sa.Column("platform", sa.String(64), nullable=False, server_default=""),
            sa.Column("arch", sa.String(64), nullable=False, server_default=""),
            sa.Column("build_type", sa.String(32), nullable=False, server_default=""),
            sa.Column("link", sa.String(32), nullable=False, server_default=""),
            sa.Column("org_slug", sa.String(255), nullable=False, server_default=""),
            sa.Column("actor", sa.String(255), nullable=False, server_default=""),
            sa.Column("size_bytes", sa.BigInteger, nullable=False, server_default="0"),
            sa.Column("detail", sa.Text, nullable=False, server_default=""),
        )
        op.create_index("ix_package_events_kind", "package_events", ["kind"])
        op.create_index("ix_package_events_name", "package_events", ["name"])
        op.create_index("ix_package_events_org_slug", "package_events", ["org_slug"])
        op.create_index("ix_package_events_created_at", "package_events", ["created_at"])
        if {"packages", "package_tombstones"} <= tables:
            conn.execute(sa.text(_BACKFILL))
    if "builders" in tables:
        cols = {c["name"] for c in insp.get_columns("builders")}
        if "system_stats" not in cols:
            op.add_column("builders", sa.Column("system_stats", sa.Text(), nullable=True))


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())
    if "builders" in tables:
        cols = {c["name"] for c in insp.get_columns("builders")}
        if "system_stats" in cols:
            op.drop_column("builders", "system_stats")
    if "package_events" in tables:
        for ix in (
            "ix_package_events_created_at",
            "ix_package_events_org_slug",
            "ix_package_events_name",
            "ix_package_events_kind",
        ):
            op.drop_index(ix, table_name="package_events")
        op.drop_table("package_events")
