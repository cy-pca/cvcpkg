"""builders.served_namespaces: multi-tenant shared builder fleet

Revision ID: 023
Revises: 022
Create Date: 2026-07-24

Implements the "Multi-tenant / shared builder fleet" roadmap item
(CVCPKG-ROADMAP.md): a builder advertises a SET of served namespaces
instead of a single ``org_slug``, so one machine can serve the public
namespace *and* one or more orgs at once, and the scheduler matches a
job to any builder whose served set contains the job's ``org_slug``.

``org_slug`` is kept as the builder's *home* namespace (identity — the
existing ``uq_builder_name_org`` is unchanged) and always belongs to the
served set. ``served_namespaces`` is a JSON-encoded list of strings
(``""`` is the public namespace), e.g. ``["", "cvc"]``.

Backfill: every existing builder served exactly its ``org_slug`` under
the old 1:1 rule, so its served set is ``[org_slug]`` — this preserves
current scheduling behaviour exactly until a builder re-registers with a
wider set.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "023"
down_revision: str | None = "022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "builders" not in insp.get_table_names():
        # Fresh databases get the full schema (already including
        # served_namespaces) from create_tables(); nothing to migrate.
        return
    columns = {c["name"] for c in insp.get_columns("builders")}
    if "served_namespaces" not in columns:
        op.add_column(
            "builders",
            sa.Column(
                "served_namespaces",
                sa.Text(),
                nullable=False,
                server_default="[]",
            ),
        )

    # Backfill: served set = [home org_slug] for every row not yet populated
    # (a fresh add leaves the server_default "[]"). Done per-row in Python so
    # the JSON is portable across SQLite / PostgreSQL and safe regardless of
    # the slug's contents.
    builders = sa.table(
        "builders",
        sa.column("id", sa.Integer),
        sa.column("org_slug", sa.Text),
        sa.column("served_namespaces", sa.Text),
    )
    rows = conn.execute(
        sa.select(builders.c.id, builders.c.org_slug).where(
            sa.or_(
                builders.c.served_namespaces == "[]",
                builders.c.served_namespaces == "",
                builders.c.served_namespaces.is_(None),
            )
        )
    ).fetchall()
    for rid, org in rows:
        conn.execute(
            builders.update()
            .where(builders.c.id == rid)
            .values(served_namespaces=json.dumps([org or ""]))
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "builders" not in insp.get_table_names():
        return
    columns = {c["name"] for c in insp.get_columns("builders")}
    if "served_namespaces" in columns:
        op.drop_column("builders", "served_namespaces")
