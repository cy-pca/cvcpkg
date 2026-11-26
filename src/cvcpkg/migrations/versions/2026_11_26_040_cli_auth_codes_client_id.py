# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""add cli_auth_codes.client_id (the client a loopback code was issued to)

Revision ID: 040
Revises: 039
Create Date: 2026-11-26

The loopback grant had one client, ``cvcpkg-cli``, so a code needed no record
of whom it was for.  A second public client (``cypkg``, which signs in through
Ariadne's ``oidc-sign-in``) means the token endpoint must redeem a code only for
the client it was issued to (RFC 6749 §4.1.3).  Codes live 60 seconds, so rows
from before this column ('') are gone long before they matter; the token
endpoint does not check a client against ''.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "040"
down_revision: str | None = "039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "cli_auth_codes" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("cli_auth_codes")}
    if "client_id" in cols:
        return
    op.add_column(
        "cli_auth_codes",
        sa.Column("client_id", sa.String(64), nullable=False, server_default=""),
    )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    if "cli_auth_codes" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("cli_auth_codes")}
    if "client_id" in cols:
        op.drop_column("cli_auth_codes", "client_id")
