# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""cli login broker: pairings, auth codes, login txns, code attempts + session refresh

Revision ID: 029
Revises: 028
Create Date: 2026-09-14

Stage 3 of the tx.wtf SSO plan: ``cvcpkg login`` makes cvcpkg.org a small
first-party authorization server.  This adds the short-lived state for its two
grants (device pairing + loopback authorization code) and the brute-force
accounting for user-code submission, and extends ``sessions`` with the
opaque-bearer + rotating-refresh columns deliberately deferred by migration 028
(``the CLI flow lands them additively``).

Every new ``sessions`` column is NULL/defaulted, so existing browser sessions —
whose cookie is a signed reference, not a stored bearer — are untouched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "029"
down_revision: str | None = "028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    if "sessions" in tables:
        cols = {c["name"] for c in insp.get_columns("sessions")}
        additive = [
            ("token_hash", sa.Column("token_hash", sa.String(64), nullable=True)),
            ("refresh_hash", sa.Column("refresh_hash", sa.String(64), nullable=True)),
            ("refresh_family", sa.Column("refresh_family", sa.String(64), nullable=True)),
            (
                "refresh_used",
                sa.Column("refresh_used", sa.Boolean(), nullable=False, server_default=sa.false()),
            ),
            (
                "client_id",
                sa.Column("client_id", sa.String(64), nullable=False, server_default=""),
            ),
            ("refresh_expires_at", sa.Column("refresh_expires_at", sa.DateTime(timezone=True))),
            ("max_lifetime_at", sa.Column("max_lifetime_at", sa.DateTime(timezone=True))),
        ]
        for name, col in additive:
            if name not in cols:
                op.add_column("sessions", col)
        indexes = {i["name"] for i in insp.get_indexes("sessions")}
        if "uq_sessions_token_hash" not in indexes:
            op.create_index("uq_sessions_token_hash", "sessions", ["token_hash"], unique=True)
        if "uq_sessions_refresh_hash" not in indexes:
            op.create_index("uq_sessions_refresh_hash", "sessions", ["refresh_hash"], unique=True)
        if "ix_sessions_refresh_family" not in indexes:
            op.create_index("ix_sessions_refresh_family", "sessions", ["refresh_family"])

    if "cli_pairings" not in tables:
        op.create_table(
            "cli_pairings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("pairing_hash", sa.String(64), nullable=False),
            sa.Column("user_code_hash", sa.String(64), nullable=False),
            sa.Column("verifier_hash", sa.String(64), nullable=False),
            sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
            sa.Column("requested_role", sa.String(32), nullable=False, server_default=""),
            sa.Column("granted_role", sa.String(32), nullable=False, server_default=""),
            sa.Column("device_label", sa.String(128), nullable=False, server_default=""),
            sa.Column("client_version", sa.String(32), nullable=False, server_default=""),
            sa.Column("platform", sa.String(64), nullable=False, server_default=""),
            sa.Column("client_ip", sa.String(64), nullable=False, server_default=""),
            sa.Column(
                "principal_id",
                sa.Integer(),
                sa.ForeignKey("principals.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column("interval_seconds", sa.Integer(), nullable=False, server_default="5"),
            sa.Column("slow_down_strikes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_poll_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("pairing_hash", name="uq_cli_pairings_pairing_hash"),
        )
        op.create_index("ix_cli_pairings_user_code_hash", "cli_pairings", ["user_code_hash"])
        op.create_index("ix_cli_pairings_expires_at", "cli_pairings", ["expires_at"])

    if "cli_auth_codes" not in tables:
        op.create_table(
            "cli_auth_codes",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code_hash", sa.String(64), nullable=False),
            sa.Column(
                "principal_id",
                sa.Integer(),
                sa.ForeignKey("principals.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(32), nullable=False),
            sa.Column("code_challenge", sa.String(64), nullable=False, server_default=""),
            sa.Column("redirect_uri", sa.Text(), nullable=False, server_default=""),
            sa.Column("device_label", sa.String(128), nullable=False, server_default=""),
            sa.Column("used", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("code_hash", name="uq_cli_auth_codes_code_hash"),
        )
        op.create_index("ix_cli_auth_codes_expires_at", "cli_auth_codes", ["expires_at"])

    if "cli_login_txns" not in tables:
        op.create_table(
            "cli_login_txns",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("txn_id", sa.String(64), nullable=False),
            sa.Column("client_id", sa.String(64), nullable=False, server_default=""),
            sa.Column("redirect_uri", sa.Text(), nullable=False, server_default=""),
            sa.Column("cli_state", sa.Text(), nullable=False, server_default=""),
            sa.Column("code_challenge", sa.String(64), nullable=False, server_default=""),
            sa.Column("requested_role", sa.String(32), nullable=False, server_default=""),
            sa.Column("device_label", sa.String(128), nullable=False, server_default=""),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("txn_id", name="uq_cli_login_txns_txn_id"),
        )
        op.create_index("ix_cli_login_txns_expires_at", "cli_login_txns", ["expires_at"])

    if "code_attempts" not in tables:
        op.create_table(
            "code_attempts",
            sa.Column("client_ip", sa.String(64), primary_key=True),
            sa.Column(
                "window_start",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("failures", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    for name in ("code_attempts", "cli_login_txns", "cli_auth_codes", "cli_pairings"):
        if name in tables:
            op.drop_table(name)

    if "sessions" in tables:
        indexes = {i["name"] for i in insp.get_indexes("sessions")}
        for ix in (
            "uq_sessions_token_hash",
            "uq_sessions_refresh_hash",
            "ix_sessions_refresh_family",
        ):
            if ix in indexes:
                op.drop_index(ix, table_name="sessions")
        cols = {c["name"] for c in insp.get_columns("sessions")}
        for name in (
            "token_hash",
            "refresh_hash",
            "refresh_family",
            "refresh_used",
            "client_id",
            "refresh_expires_at",
            "max_lifetime_at",
        ):
            if name in cols:
                op.drop_column("sessions", name)
