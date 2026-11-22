# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""pg_trgm index for /v1/search's free-text filter

Revision ID: 039
Revises: 038
Create Date: 2026-11-22

``/v1/search?q=`` matches ``ILIKE '%q%'`` against 12 columns of ``packages``.
An unanchored LIKE can use no btree, so every query a search ran evaluated
those 12 ILIKEs on every row: ~19k rows on cvcpkg.org.

This is a GIN trigram index over the 12 columns joined by spaces.
``db_stores._search_match`` adds the same pattern against that expression as an
extra, redundant condition (a column that contains q puts it in the joined
text too), which Postgres answers from the index.  It then checks the
per-column ILIKEs only on the rows the index found.  The expression here and
``db_stores._search_text`` must stay identical, or the planner will not match
them; ``tests/unit/test_search_relevance.py`` checks that.

Postgres only: SQLite and MySQL scan, as before.  The index needs the pg_trgm
extension, which postgres:16's contrib ships and which is a trusted extension
(PostgreSQL 13+), so a database owner may create it.  A server without it gets
a warning and no index, and searches the slow way.
"""

from __future__ import annotations

import warnings
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "039"
down_revision: str | None = "038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INDEX = "ix_packages_search_trgm"

# db_stores._SEARCH_COLUMNS, in that order, joined by spaces.  All NOT NULL.
SEARCH_TEXT = (
    "name || ' ' || version || ' ' || platform || ' ' || arch || ' ' || "
    "build_type || ' ' || link || ' ' || description || ' ' || tags || ' ' || "
    "maintainer || ' ' || license || ' ' || release_tag || ' ' || org_slug"
)


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != "postgresql":
        return
    if "packages" not in sa.inspect(conn).get_table_names():
        return
    available = conn.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'pg_trgm'")
    ).scalar()
    if not available:
        warnings.warn(
            "pg_trgm is not available on this server: /v1/search?q= stays a full scan",
            stacklevel=1,
        )
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        f"CREATE INDEX IF NOT EXISTS {INDEX} ON packages USING gin (({SEARCH_TEXT}) gin_trgm_ops)"
    )
    # Statistics on the indexed expression, so the planner estimates how many
    # rows a pattern matches before choosing the index or a scan.
    op.execute("ANALYZE packages")


def downgrade() -> None:
    # The extension stays: other objects may use it by now.
    if op.get_bind().dialect.name == "postgresql":
        op.execute(f"DROP INDEX IF EXISTS {INDEX}")
