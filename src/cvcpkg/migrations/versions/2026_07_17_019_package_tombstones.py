# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""package_tombstones

Revision ID: 019
Revises: 018
Create Date: 2026-07-17

A nuked bundle's ``packages`` row is hard-deleted (freeing the archive-slot for
republish), so nothing recorded that it had ever existed or WHY it went away --
a consumer whose lockfile pinned it got a bare 404, indistinguishable from a
package that never existed.  This table is a lightweight tombstone: one row per
nuked bundle variant, carrying the reason ("manual" = an admin's ``cvcpkg
nuke``, "retention" = fell off the yank-retention schedule), who did it, when,
and a little forensic context.  It drives a 410 Gone for pinned consumers and
survives audit-log rotation.

Tombstones hold no archive bytes, so they are cheap to keep; there is no
retention on them.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "019"
down_revision: str | None = "018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "package_tombstones" in insp.get_table_names():
        return
    op.create_table(
        "package_tombstones",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.String(128), nullable=False),
        sa.Column("platform", sa.String(64), nullable=False, server_default=""),
        sa.Column("arch", sa.String(64), nullable=False, server_default=""),
        sa.Column("build_type", sa.String(32), nullable=False, server_default=""),
        sa.Column("link", sa.String(32), nullable=False, server_default=""),
        sa.Column("org_slug", sa.String(255), nullable=False, server_default=""),
        # Archive basename (from the row's archive_url); the download endpoint
        # looks a tombstone up by this to answer 410 Gone instead of 404.
        sa.Column("filename", sa.String(512), nullable=False, server_default=""),
        # "manual" (cvcpkg nuke) or "retention" (fell off the schedule).
        sa.Column("reason", sa.String(32), nullable=False),
        # Token name for a manual nuke, or "retention-gc" for the GC.
        sa.Column("nuked_by", sa.String(255), nullable=False, server_default=""),
        sa.Column(
            "nuked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        # Forensic context copied off the row before it was deleted.
        sa.Column("size_bytes", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(64), nullable=False, server_default=""),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("yanked_at", sa.DateTime(timezone=True), nullable=True),
    )
    # Lookups are "what happened to <name> [<version>]" -- for the 410 and the
    # per-package history view.
    op.create_index(
        "ix_package_tombstones_name_version",
        "package_tombstones",
        ["name", "version"],
    )
    # The download 410 looks up by archive filename.
    op.create_index(
        "ix_package_tombstones_filename",
        "package_tombstones",
        ["filename"],
    )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "package_tombstones" not in insp.get_table_names():
        return
    op.drop_index("ix_package_tombstones_filename", table_name="package_tombstones")
    op.drop_index("ix_package_tombstones_name_version", table_name="package_tombstones")
    op.drop_table("package_tombstones")
