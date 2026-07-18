"""backfill packages.origin_upstream from published_by

Revision ID: 022
Revises: 021
Create Date: 2026-07-18

Migration 020 added ``origin_upstream`` without a backfill, on the reasoning
that stamping existing rows would "hand reconciliation authority over data whose
provenance nobody actually recorded".  Both halves of that turned out to be
wrong, and the combination made the whole upstream-authoritative feature a
silent no-op on every server that already had a catalogue.

Provenance *was* recorded.  The populate importer has long written
``published_by = f"populate:{upstream}"`` -- the exact upstream, per row.  So
this backfill does not guess a configured value; it reads the one already
stored, which is precisely the evidence 020 wanted and assumed absent.

And rows are *not* re-stamped naturally, as 020 claimed.  Populate is add-only:
it skips any key already present before reaching ``add_package``, the sole
writer of ``origin_upstream``.  A pre-existing row therefore keeps
``origin_upstream = ""`` forever, and both ``mirrored_keys()`` and
``reconcile_from_upstream()`` filter on ``origin_upstream == upstream`` -- so
upstream's yanks and nukes reach none of it, indefinitely and without a warning.

Scope is deliberately narrow, preserving 020's actual safety goal:

* ``origin_upstream = ''`` only -- never overwrite a stamp already made.
* ``published_by LIKE 'populate:%'`` -- a locally published package (a builder
  name, an operator) is left alone, so an edge's own packages can never be
  yanked merely because some upstream never had them.  That was 020's real
  concern and it remains honoured.
* public rows only -- populate never imports org-scoped packages, so a
  populate-stamped org row would be anomalous and is not touched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "022"
down_revision: str | None = "021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREFIX = "populate:"


def upgrade() -> None:
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("packages")}
    if "origin_upstream" not in existing or "published_by" not in existing:
        return

    packages = sa.table(
        "packages",
        sa.column("origin_upstream", sa.Text),
        sa.column("published_by", sa.Text),
        sa.column("org_slug", sa.Text),
    )

    # substr() is 1-indexed in both SQLite and PostgreSQL, so the upstream URL
    # starts one past the prefix.  published_by was written from the same
    # rstrip("/")-normalised value that reconciliation compares against, so the
    # result matches mirrored_keys() exactly rather than approximately.
    op.execute(
        packages.update()
        .where(
            sa.and_(
                sa.or_(
                    packages.c.origin_upstream == "",
                    packages.c.origin_upstream.is_(None),
                ),
                packages.c.published_by.like(f"{_PREFIX}%"),
                sa.or_(packages.c.org_slug == "", packages.c.org_slug.is_(None)),
            )
        )
        .values(
            origin_upstream=sa.func.substr(packages.c.published_by, len(_PREFIX) + 1),
        )
    )


def downgrade() -> None:
    # Clear only what this migration could have set, identified the same way it
    # was chosen.  A stamp written by the importer since the upgrade is
    # indistinguishable from a backfilled one and is cleared too; that is
    # harmless, because the importer re-stamps on the next import of that row.
    bind = op.get_bind()
    existing = {c["name"] for c in sa.inspect(bind).get_columns("packages")}
    if "origin_upstream" not in existing or "published_by" not in existing:
        return

    packages = sa.table(
        "packages",
        sa.column("origin_upstream", sa.Text),
        sa.column("published_by", sa.Text),
    )
    op.execute(
        packages.update()
        .where(packages.c.published_by.like(f"{_PREFIX}%"))
        .values(origin_upstream="")
    )
