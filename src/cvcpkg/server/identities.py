# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Principal storage — turning an IdP subject into a durable cvcpkg identity.

The whole difficulty here is one column.  ``principals.name`` is not a display
string: organization membership is keyed on a bare name
(``org_members.token_name``), compared with ``==``, with no check that the
named token still exists or is still live.  So allotting a name is granting
whatever authority that name already carries.

Two consequences drive this module:

* **Resolution is on ``(issuer, subject)``, never email.** ``tokens.email`` is
  unvalidated free text, self-settable via ``PATCH /v1/tokens/{name}/email``,
  carries no unique constraint, and is readable through an endpoint that
  documents itself as requiring no authentication.  Matching on it would let
  whoever types an address first inherit the account.
* **The taken-set includes revoked tokens.**  ``uq_tokens_active_name`` is a
  *partial* index and ``revoke()`` sets one boolean without touching
  ``org_members``.  A revoked token named ``joe`` still owns every membership
  row it ever had, so handing ``joe`` to a new principal would hand over those
  orgs.  Checking only live names is the hole this closes.
"""

from __future__ import annotations

import datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from cvcpkg.server import principals as _principals
from cvcpkg.server.db import (
    BuildJobRow,
    OrgMemberRow,
    PrincipalRow,
    TokenRow,
    atomic_session,
    get_session,
)


class DbPrincipalStore:
    """Principals, resolved on ``(issuer, subject)``."""

    async def taken_names(self) -> set[str]:
        """Every name that could already carry authority.

        Four sources, each for a reason:

        * **all** ``tokens.name``, revoked included — see the module docstring;
        * ``org_members.token_name`` — ``add_member`` writes a free string with
          no existence check, so every typo an admin ever made is a live
          membership row attached to a claimable name;
        * ``build_jobs.claimed_by`` — the only other predicate in the server
          that authorizes on a name (a builder re-claiming its own job);
        * ``principals.name`` — the obvious one.
        """
        async with get_session() as session:
            names: set[str] = set()
            for stmt in (
                select(TokenRow.name),
                select(OrgMemberRow.token_name),
                select(BuildJobRow.claimed_by).where(BuildJobRow.claimed_by != ""),
                select(PrincipalRow.name),
            ):
                rows = await session.execute(stmt)
                names.update(str(r) for (r,) in rows if r)
            return names | set(_principals.RESERVED)

    async def principal_names(self) -> set[str]:
        """Just the principal names — the reverse guard for token creation."""
        async with get_session() as session:
            rows = await session.execute(select(PrincipalRow.name))
            return {str(r) for (r,) in rows if r}

    async def by_name(self, name: str) -> PrincipalRow | None:
        async with get_session() as session:
            return (
                (await session.execute(select(PrincipalRow).where(PrincipalRow.name == name)))
                .scalars()
                .first()
            )

    async def by_id(self, principal_id: int) -> PrincipalRow | None:
        async with get_session() as session:
            return (
                (await session.execute(select(PrincipalRow).where(PrincipalRow.id == principal_id)))
                .scalars()
                .first()
            )

    async def list_all(self, *, q: str = "") -> list[PrincipalRow]:
        async with get_session() as session:
            stmt = select(PrincipalRow).order_by(PrincipalRow.name)
            if q:
                like = f"%{q.lower()}%"
                stmt = stmt.where(func.lower(PrincipalRow.name).like(like))
            return list((await session.execute(stmt)).scalars().all())

    async def upsert_from_claims(
        self,
        issuer: str,
        subject: str,
        claims: dict,
        role: str,
        *,
        allow_names: frozenset[str] | set[str] | None = None,
    ) -> tuple[PrincipalRow, bool]:
        """Resolve or create the principal for ``(issuer, subject)``.

        Returns ``(row, email_changed)``.  ``email_changed`` is surfaced rather
        than swallowed because a changed email on an unchanged subject is the
        only signal available that an IdP may have reused a subject, which is
        otherwise completely silent.

        ``allow_names`` exempts names from the taken-set.  It exists for the
        token sign-in path: the taken-set includes every ``tokens.name``, which
        includes the very token doing the authenticating — so without it, token
        ``joe`` always lands on principal ``joe-2``, sees none of ``joe``'s
        organizations, and cannot rename back to the name it already holds.
        Pass only a name the caller has just authenticated as.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        email = str(claims.get("email") or "")
        display = str(claims.get("name") or claims.get("preferred_username") or "")

        async with atomic_session() as session:
            row = (
                (
                    await session.execute(
                        select(PrincipalRow).where(
                            PrincipalRow.issuer == issuer,
                            PrincipalRow.subject == subject,
                        )
                    )
                )
                .scalars()
                .first()
            )

            if row is not None:
                email_changed = bool(
                    email and row.first_seen_email and email != row.first_seen_email
                )
                row.email = email or row.email
                row.display_name = display or row.display_name
                row.last_role = role
                row.last_login_at = now
                return row, email_changed

            base = _principals.sanitize_principal_name(claims)
            taken = await self._taken_names_in(session) - set(allow_names or ())
            for candidate in _principals.collision_candidates(base):
                if candidate in taken:
                    continue
                row = PrincipalRow(
                    name=candidate,
                    issuer=issuer,
                    subject=subject,
                    email=email,
                    first_seen_email=email,
                    display_name=display,
                    last_role=role,
                    last_login_at=now,
                )
                session.add(row)
                try:
                    # Flush inside the loop so a racing INSERT of the same name
                    # surfaces here and we simply try the next candidate,
                    # rather than escaping as a 500 at commit time.
                    await session.flush()
                except IntegrityError:
                    await session.rollback()
                    taken.add(candidate)
                    continue
                return row, False

            raise ValueError(f"could not allocate a principal name for {base!r}")

    async def _taken_names_in(self, session) -> set[str]:
        names: set[str] = set()
        for stmt in (
            select(TokenRow.name),
            select(OrgMemberRow.token_name),
            select(BuildJobRow.claimed_by).where(BuildJobRow.claimed_by != ""),
            select(PrincipalRow.name),
        ):
            rows = await session.execute(stmt)
            names.update(str(r) for (r,) in rows if r)
        return names | set(_principals.RESERVED)

    async def set_disabled(self, name: str, disabled: bool) -> bool:
        async with atomic_session() as session:
            row = (
                (await session.execute(select(PrincipalRow).where(PrincipalRow.name == name)))
                .scalars()
                .first()
            )
            if row is None:
                return False
            row.disabled = disabled
            return True

    async def rename(self, principal_id: int, new_name: str) -> bool:
        """Rename a principal that has not yet been used for anything.

        The caller establishes "unused"; this only enforces that the new name
        is allocatable.  A principal that has minted a token, joined an org or
        published cannot be renamed, because a rename cannot rewrite
        ``packages.published_by`` or ``audit_log.actor`` without falsifying
        history.
        """
        if not _principals.is_valid_name(new_name):
            return False
        async with atomic_session() as session:
            taken = await self._taken_names_in(session)
            if new_name in taken:
                return False
            row = (
                (await session.execute(select(PrincipalRow).where(PrincipalRow.id == principal_id)))
                .scalars()
                .first()
            )
            if row is None:
                return False
            row.name = new_name
            try:
                await session.flush()
            except IntegrityError:
                return False
            return True
