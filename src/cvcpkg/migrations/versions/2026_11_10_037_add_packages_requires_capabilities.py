# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""add packages.requires_capabilities (host capabilities a bundle needs)

Revision ID: 037
Revises: 036
Create Date: 2026-11-10

A recipe that declares ``requires_capabilities: [cuda]`` (libcvc-cuda, which
also ``provides: [libcvc]``) packs it into the bundle manifest, and the client
resolver drops a provider whose capabilities the installing host lacks.  The
server dropped the list at the publish boundary, as it once did ``provides``
(migration 026), so the catalog never served it and the filter never fired: a
dependency on ``libcvc`` resolved to libcvc-cuda on hosts without CUDA whenever
libcvc-cuda had the higher version.  On 2026-11-10 that broke a GitHub
ubuntu runner's CMake configure (``cvc::cvc`` links ``CUDA::cudart_static``).

JSON-encoded list of names; '[]' for nearly every package.  Rows published
before this column existed read '[]' until the backfill-deps admin endpoint
fills them.  Not the build-job routing column of the same idea
(``build_jobs.required_capabilities``, migration 025).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "037"
down_revision: str | None = "036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("packages")}
    if "requires_capabilities" in cols:
        return
    op.add_column(
        "packages",
        sa.Column("requires_capabilities", sa.Text(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "packages" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("packages")}
    if "requires_capabilities" in cols:
        op.drop_column("packages", "requires_capabilities")
