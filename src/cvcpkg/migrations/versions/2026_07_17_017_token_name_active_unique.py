# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""tokens.name: partial unique on active rows (reuse revoked names)

Revision ID: 017
Revises: 016
Create Date: 2026-07-17

The initial schema put a full ``UNIQUE(name)`` on ``tokens``.  That forbids
reusing a name once its token is revoked — but the app-level guard (and the
YAML TokenStore) only forbid a *second active* token with the same name, so
recreating a revoked name raised an IntegrityError (HTTP 500) on the DB
backend while succeeding on YAML.  Token rotation, which leaves revoked rows
behind, makes the collision more likely.

Fix: replace the full unique with a partial unique index over non-revoked
rows (``uq_tokens_active_name``).  Postgres and SQLite support partial
indexes; MySQL does not, so there we keep a full ``UNIQUE(name)`` (just
renamed for schema parity with ``create_tables()``) and rely on
``DbTokenStore.create()`` turning the IntegrityError into a clean 409.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# SQLite reflects the initial schema's unnamed UNIQUE(name) as name=None, so a
# naming convention is supplied to batch mode to give it a droppable name.
_SQLITE_NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def _drop_full_name_unique(insp) -> None:
    """Drop whatever full UNIQUE constraint covers exactly the ``name`` column."""
    for uc in insp.get_unique_constraints("tokens"):
        if uc["column_names"] == ["name"] and uc.get("name"):
            op.drop_constraint(uc["name"], "tokens", type_="unique")
            return


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    insp = sa.inspect(conn)
    if "tokens" not in insp.get_table_names():
        # Fresh databases get the full schema (already including
        # uq_tokens_active_name) from create_tables(); nothing to migrate.
        return
    if "uq_tokens_active_name" in {ix["name"] for ix in insp.get_indexes("tokens")}:
        # Already migrated (or built by create_tables()).
        return

    if dialect == "sqlite":
        with op.batch_alter_table("tokens", naming_convention=_SQLITE_NAMING) as batch_op:
            batch_op.drop_constraint("uq_tokens_name", type_="unique")
        op.create_index(
            "uq_tokens_active_name",
            "tokens",
            ["name"],
            unique=True,
            sqlite_where=sa.text("revoked = 0"),
        )
    elif dialect == "postgresql":
        _drop_full_name_unique(insp)
        op.create_index(
            "uq_tokens_active_name",
            "tokens",
            ["name"],
            unique=True,
            postgresql_where=sa.text("revoked = false"),
        )
    else:
        # MySQL and other backends without partial-index support: keep a full
        # UNIQUE(name), only normalised to the new name for schema parity.
        _drop_full_name_unique(insp)
        op.create_index("uq_tokens_active_name", "tokens", ["name"], unique=True)


def downgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name
    insp = sa.inspect(conn)
    if "tokens" not in insp.get_table_names():
        return
    if "uq_tokens_active_name" not in {ix["name"] for ix in insp.get_indexes("tokens")}:
        return

    if dialect == "sqlite":
        op.drop_index("uq_tokens_active_name", table_name="tokens")
        with op.batch_alter_table("tokens", naming_convention=_SQLITE_NAMING) as batch_op:
            batch_op.create_unique_constraint("uq_tokens_name", ["name"])
    else:
        op.drop_index("uq_tokens_active_name", table_name="tokens")
        op.create_unique_constraint("tokens_name_key", "tokens", ["name"])
