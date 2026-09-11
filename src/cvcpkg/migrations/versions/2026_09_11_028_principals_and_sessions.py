# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""add principals + sessions; tokens.principal_id (SSO identities)

Revision ID: 028
Revises: 027
Create Date: 2026-09-11

cvcpkg has had no concept of a *person*.  Identity was a token name, and the
admin session cookie carried nothing but an expiry and a signature — which is
why the dashboard audit log records the literal string "admin-ui" as the actor
and why the public site has never had a login at all.  Wiring cvcpkg.org to
tx.wtf makes that gap the binding constraint: the server can map an IdP group
onto a role, but it has nowhere to put the human the role belongs to.

``principals`` is that place.  A principal is resolved on ``(issuer, subject)``
and **never** on email — ``tokens.email`` is unvalidated free text, is
self-settable through ``PATCH /v1/tokens/{name}/email``, has no unique
constraint, and is publicly enumerable through an endpoint that documents
itself as requiring no authentication.  Auto-linking on it would hand an
account to whoever typed the address first.

``principals.name`` is the load-bearing column.  Organization membership is
keyed on a bare string (``org_members.token_name``) compared with ``==`` and no
liveness check, so the name a principal is allotted is exactly what grants
access to a private org.  Allocation is therefore guarded against every name
that could already carry authority — including **revoked** tokens, because
``uq_tokens_active_name`` is a partial index and revoking a token flips one
boolean without touching its membership rows.

``tokens.principal_id`` is nullable and there is **no backfill**.  Every
existing token keeps NULL and behaves exactly as before; only tokens minted
through the new self-service surface carry an owner.  That nullable column is
what lets a delegated token present as its principal to every authorization
predicate in the server without editing any of them.

Deliberately absent: refresh tokens.  A browser session has no refresh
credential, so ``refresh_hash``/``refresh_family``/``max_lifetime_at`` land
additively with the CLI flow rather than shipping unused here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "028"
down_revision: str | None = "027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    if "principals" not in tables:
        op.create_table(
            "principals",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("issuer", sa.String(255), nullable=False),
            sa.Column("subject", sa.String(255), nullable=False),
            sa.Column("email", sa.String(255), nullable=False, server_default=""),
            # Kept so that an email change on an existing (issuer, subject) is
            # detectable.  Subject reuse by an IdP is otherwise silent.
            sa.Column("first_seen_email", sa.String(255), nullable=False, server_default=""),
            sa.Column("display_name", sa.String(255), nullable=False, server_default=""),
            sa.Column("last_role", sa.String(32), nullable=False, server_default="reader"),
            sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("name", name="uq_principals_name"),
            sa.UniqueConstraint("issuer", "subject", name="uq_principals_identity"),
        )

    if "sessions" not in tables:
        op.create_table(
            "sessions",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "principal_id",
                sa.Integer(),
                sa.ForeignKey("principals.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("role", sa.String(32), nullable=False),
            sa.Column("device_label", sa.String(128), nullable=False, server_default=""),
            sa.Column("ip_at_issue", sa.String(64), nullable=False, server_default=""),
            sa.Column(
                "issued_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False, index=True),
            sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        )

    if "tokens" in tables:
        cols = {c["name"] for c in insp.get_columns("tokens")}
        if "principal_id" not in cols:
            op.add_column("tokens", sa.Column("principal_id", sa.Integer(), nullable=True))
            op.create_index("ix_tokens_principal_id", "tokens", ["principal_id"])


def downgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    tables = set(insp.get_table_names())

    if "tokens" in tables:
        cols = {c["name"] for c in insp.get_columns("tokens")}
        if "principal_id" in cols:
            indexes = {i["name"] for i in insp.get_indexes("tokens")}
            if "ix_tokens_principal_id" in indexes:
                op.drop_index("ix_tokens_principal_id", table_name="tokens")
            op.drop_column("tokens", "principal_id")
    if "sessions" in tables:
        op.drop_table("sessions")
    if "principals" in tables:
        op.drop_table("principals")
