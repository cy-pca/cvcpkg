# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""widen packages.license to 1024 characters

Revision ID: 030
Revises: 029
Create Date: 2026-10-13

``packages.license`` was VARCHAR(128).  An SPDX expression for a bundle that
statically links several differently-licensed libraries runs past that:
litecoin-core's and dogecoin-core's is 129 characters (MIT, BSD-2/3-Clause,
BSL-1.0, Sleepycat, OpenSSL and ZeroMQ's LGPL-3.0-or-later WITH
LGPL-3.0-linking-exception).  PostgreSQL refused the INSERT, so every publish
of either answered 500 after the whole upload.

Widening a VARCHAR is a catalog-only change on PostgreSQL (no table rewrite)
and an in-place MODIFY on MySQL.  SQLite does not enforce VARCHAR lengths, so
there is nothing to do there, and rebuilding ``packages`` through
batch_alter_table only to restate the declared length is not worth it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "030"
down_revision: str | None = "029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _alter(from_len: int, to_len: int) -> None:
    conn = op.get_bind()
    if conn.dialect.name == "sqlite":
        return
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    col = {c["name"]: c for c in insp.get_columns("packages")}.get("license")
    if col is None or getattr(col["type"], "length", None) == to_len:
        return
    op.alter_column(
        "packages",
        "license",
        type_=sa.String(to_len),
        existing_type=sa.String(from_len),
        existing_nullable=False,
        existing_server_default="",
    )


def upgrade() -> None:
    _alter(128, 1024)


def downgrade() -> None:
    # Fails on PostgreSQL/MySQL while any row holds a licence over 128
    # characters; shorten or delete those rows first.
    _alter(1024, 128)
