"""Database-backed stores for cvcpkg-server.

These classes implement the same interfaces as the YAML-file-based
TokenStore and AuditLog but persist to PostgreSQL via SQLAlchemy.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import secrets
from pathlib import Path

from sqlalchemy import distinct, or_, select, update
from sqlalchemy import func as sa_func

from cvcpkg.server.db import (
    AuditRow,
    BuilderRow,
    BuildJobDepRow,
    BuildJobRow,
    DownloadEventRow,
    MirrorRow,
    OrganizationRow,
    OrgMemberRow,
    PackageRow,
    RecipeRow,
    TagRow,
    TelemetryEventRow,
    TokenRequestRow,
    TokenRow,
    WebhookRow,
    get_session,
)
from cvcpkg.server.models import (
    AuditAction,
    AuditEntry,
    BuilderInfo,
    BuilderStatus,
    BuildJobInfo,
    BuildJobStatus,
    MirrorInfo,
    OrgInfo,
    OrgMember,
    OrgRole,
    PackageInfo,
    RecipeInfo,
    TagInfo,
    TokenRecord,
    TokenRequestRecord,
    TokenRequestStatus,
    TokenRole,
    UserProfileResponse,
    WebhookInfo,
)

# ── HMAC key management ────────────────────────────────────────

_HMAC_KEY_FILE = "hmac_key"


def _ensure_hmac_key(state_dir: Path) -> bytes:
    """Return the HMAC key, creating it if it doesn't exist."""
    key_path = state_dir / _HMAC_KEY_FILE
    if key_path.is_file():
        return key_path.read_bytes()
    state_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    key_path.write_bytes(key)
    key_path.chmod(0o600)
    return key


def _hash_token(raw_token: str, hmac_key: bytes) -> str:
    return hmac.new(hmac_key, raw_token.encode(), hashlib.sha256).hexdigest()


# ── DB Token Store ──────────────────────────────────────────────


class DbTokenStore:
    """Token management backed by the ``tokens`` table."""

    def __init__(self, state_dir: Path) -> None:
        self._hmac_key = _ensure_hmac_key(state_dir)

    async def create(
        self,
        name: str,
        role: TokenRole = TokenRole.publisher,
        expires_in_days: int | None = None,
        email: str = "",
        description: str = "",
        metadata: str = "",
    ) -> str:
        async with get_session() as session:
            existing = await session.execute(
                select(TokenRow).where(
                    TokenRow.name == name,
                    TokenRow.revoked == False,  # noqa: E712
                )
            )
            if existing.scalars().first() is not None:
                raise ValueError(f"active token named '{name}' already exists")

            raw = f"cvctok_{secrets.token_urlsafe(32)}"
            token_hash = _hash_token(raw, self._hmac_key)

            expires_at = None
            if expires_in_days is not None:
                expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
                    days=expires_in_days
                )

            row = TokenRow(
                name=name,
                role=role.value,
                token_hash=token_hash,
                email=email,
                description=description,
                user_metadata=metadata,
                expires_at=expires_at,
            )
            session.add(row)
        return raw

    async def verify(self, raw_token: str) -> TokenRecord | None:
        token_hash = _hash_token(raw_token, self._hmac_key)
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            result = await session.execute(
                select(TokenRow).where(TokenRow.token_hash == token_hash)
            )
            row = result.scalars().first()
            if row is None:
                return None
            if row.revoked:
                return None
            if row.expires_at is not None and row.expires_at < now:
                return None
            return TokenRecord(
                name=row.name,
                role=TokenRole(row.role),
                token_hash=row.token_hash,
                email=row.email,
                description=row.description,
                metadata=row.user_metadata,
                created_at=row.created_at,
                expires_at=row.expires_at,
                revoked=row.revoked,
            )

    async def revoke(self, name: str) -> bool:
        async with get_session() as session:
            result = await session.execute(
                update(TokenRow)
                .where(TokenRow.name == name, TokenRow.revoked == False)  # noqa: E712
                .values(revoked=True)
            )
            return result.rowcount > 0

    async def update_email(self, name: str, email: str) -> bool:
        async with get_session() as session:
            result = await session.execute(
                update(TokenRow)
                .where(TokenRow.name == name, TokenRow.revoked == False)  # noqa: E712
                .values(email=email)
            )
            return result.rowcount > 0

    async def update_profile(
        self,
        name: str,
        description: str | None = None,
        metadata: str | None = None,
    ) -> bool:
        """Update profile fields for a token by name."""
        values: dict = {}
        if description is not None:
            values["description"] = description
        if metadata is not None:
            values["user_metadata"] = metadata
        if not values:
            return True  # nothing to update
        async with get_session() as session:
            result = await session.execute(
                update(TokenRow)
                .where(TokenRow.name == name, TokenRow.revoked == False)  # noqa: E712
                .values(**values)
            )
            return result.rowcount > 0

    async def get_public_profile(self, name: str) -> UserProfileResponse | None:
        """Look up a user by name, returning public profile info."""
        async with get_session() as session:
            result = await session.execute(
                select(TokenRow).where(
                    TokenRow.name == name,
                    TokenRow.revoked == False,  # noqa: E712
                )
            )
            row = result.scalars().first()
            if row is None:
                return None
            # Count packages published by this user
            pkg_count_result = await session.execute(
                select(sa_func.count(PackageRow.id)).where(PackageRow.published_by == name)
            )
            pkg_count = pkg_count_result.scalar() or 0
            return UserProfileResponse(
                name=row.name,
                role=row.role,
                email=row.email,
                description=row.description,
                metadata=row.user_metadata,
                packages_published=pkg_count,
                created_at=row.created_at,
            )

    async def get_profile_by_email(self, email: str) -> UserProfileResponse | None:
        """Look up a user by email, returning public profile info."""
        async with get_session() as session:
            result = await session.execute(
                select(TokenRow).where(
                    TokenRow.email == email,
                    TokenRow.revoked == False,  # noqa: E712
                )
            )
            row = result.scalars().first()
            if row is None:
                return None
            # Count packages published by this user
            pkg_count_result = await session.execute(
                select(sa_func.count(PackageRow.id)).where(PackageRow.published_by == row.name)
            )
            pkg_count = pkg_count_result.scalar() or 0
            return UserProfileResponse(
                name=row.name,
                role=row.role,
                email=row.email,
                description=row.description,
                metadata=row.user_metadata,
                packages_published=pkg_count,
                created_at=row.created_at,
            )

    async def search_users(
        self,
        *,
        name: str = "",
        email: str = "",
        role: str = "",
        org: str = "",
        has_published: bool | None = None,
        sort_by: str = "name",
        sort_order: str = "asc",
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[UserProfileResponse], int]:
        """Search active users with optional filters and pagination.

        Supports sorting by ``name``, ``email``, or ``packages_published``.
        """
        async with get_session() as session:
            # Subquery: count packages per publisher
            pkg_count_sq = (
                select(
                    PackageRow.published_by,
                    sa_func.count(PackageRow.id).label("pkg_count"),
                )
                .group_by(PackageRow.published_by)
                .subquery()
            )

            q = (
                select(
                    TokenRow,
                    sa_func.coalesce(pkg_count_sq.c.pkg_count, 0).label("pkg_count"),
                )
                .outerjoin(pkg_count_sq, TokenRow.name == pkg_count_sq.c.published_by)
                .where(TokenRow.revoked == False)  # noqa: E712
            )
            count_q = (
                select(sa_func.count(TokenRow.id))
                .outerjoin(pkg_count_sq, TokenRow.name == pkg_count_sq.c.published_by)
                .where(TokenRow.revoked == False)  # noqa: E712
            )

            if name:
                q = q.where(TokenRow.name.ilike(f"%{name}%"))
                count_q = count_q.where(TokenRow.name.ilike(f"%{name}%"))
            if email:
                q = q.where(TokenRow.email.ilike(f"%{email}%"))
                count_q = count_q.where(TokenRow.email.ilike(f"%{email}%"))
            if role:
                q = q.where(TokenRow.role == role)
                count_q = count_q.where(TokenRow.role == role)
            if org:
                org_member_names = (
                    select(OrgMemberRow.token_name)
                    .join(OrganizationRow, OrgMemberRow.org_id == OrganizationRow.id)
                    .where(OrganizationRow.slug == org)
                )
                q = q.where(TokenRow.name.in_(org_member_names))
                count_q = count_q.where(TokenRow.name.in_(org_member_names))
            if has_published is True:
                q = q.where(sa_func.coalesce(pkg_count_sq.c.pkg_count, 0) > 0)
                count_q = count_q.where(sa_func.coalesce(pkg_count_sq.c.pkg_count, 0) > 0)
            elif has_published is False:
                q = q.where(sa_func.coalesce(pkg_count_sq.c.pkg_count, 0) == 0)
                count_q = count_q.where(sa_func.coalesce(pkg_count_sq.c.pkg_count, 0) == 0)

            # Sorting
            if sort_by == "packages_published":
                order_col = sa_func.coalesce(pkg_count_sq.c.pkg_count, 0)
            elif sort_by == "email":
                order_col = TokenRow.email
            else:
                order_col = TokenRow.name

            if sort_order == "desc":
                q = q.order_by(order_col.desc())
            else:
                q = q.order_by(order_col.asc())

            total_result = await session.execute(count_q)
            total = total_result.scalar() or 0

            q = q.offset(offset).limit(limit)
            result = await session.execute(q)

            users = []
            for row_tuple in result.all():
                token_row = row_tuple[0]
                pkg_count = row_tuple[1]
                users.append(
                    UserProfileResponse(
                        name=token_row.name,
                        role=token_row.role,
                        email=token_row.email,
                        description=token_row.description,
                        metadata=token_row.user_metadata,
                        packages_published=pkg_count,
                        created_at=token_row.created_at,
                    )
                )
            return users, total

    async def count_packages_by_user(self, name: str) -> int:
        """Count the number of packages published by a user."""
        async with get_session() as session:
            result = await session.execute(
                select(sa_func.count(PackageRow.id)).where(PackageRow.published_by == name)
            )
            return result.scalar() or 0

    async def list_tokens(self) -> list[TokenRecord]:
        async with get_session() as session:
            result = await session.execute(select(TokenRow).order_by(TokenRow.created_at))
            return [
                TokenRecord(
                    name=row.name,
                    role=TokenRole(row.role),
                    token_hash=row.token_hash,
                    email=row.email,
                    description=row.description,
                    metadata=row.user_metadata,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                    revoked=row.revoked,
                )
                for row in result.scalars().all()
            ]


# ── DB Token Request Store ──────────────────────────────────────


class DbTokenRequestStore:
    """Manage pending token registration requests."""

    async def create(self, name: str, email: str, role: TokenRole) -> TokenRequestRecord:
        async with get_session() as session:
            row = TokenRequestRow(
                name=name,
                email=email,
                role=role.value,
            )
            session.add(row)
            await session.flush()
            record = TokenRequestRecord(
                id=row.id,
                name=row.name,
                email=row.email,
                role=TokenRole(row.role),
                status=TokenRequestStatus(row.status),
                created_at=row.created_at,
            )
        return record

    async def list_requests(
        self, status: TokenRequestStatus | None = None
    ) -> list[TokenRequestRecord]:
        async with get_session() as session:
            stmt = select(TokenRequestRow).order_by(TokenRequestRow.created_at.desc())
            if status is not None:
                stmt = stmt.where(TokenRequestRow.status == status.value)
            result = await session.execute(stmt)
            return [
                TokenRequestRecord(
                    id=row.id,
                    name=row.name,
                    email=row.email,
                    role=TokenRole(row.role),
                    status=TokenRequestStatus(row.status),
                    reviewed_by=row.reviewed_by,
                    created_at=row.created_at,
                    resolved_at=row.resolved_at,
                )
                for row in result.scalars().all()
            ]

    async def get(self, request_id: int) -> TokenRequestRecord | None:
        async with get_session() as session:
            result = await session.execute(
                select(TokenRequestRow).where(TokenRequestRow.id == request_id)
            )
            row = result.scalars().first()
            if row is None:
                return None
            return TokenRequestRecord(
                id=row.id,
                name=row.name,
                email=row.email,
                role=TokenRole(row.role),
                status=TokenRequestStatus(row.status),
                reviewed_by=row.reviewed_by,
                created_at=row.created_at,
                resolved_at=row.resolved_at,
            )

    async def resolve(self, request_id: int, status: TokenRequestStatus, reviewed_by: str) -> bool:
        async with get_session() as session:
            result = await session.execute(
                update(TokenRequestRow)
                .where(
                    TokenRequestRow.id == request_id,
                    TokenRequestRow.status == TokenRequestStatus.pending.value,
                )
                .values(
                    status=status.value,
                    reviewed_by=reviewed_by,
                    resolved_at=datetime.datetime.now(datetime.timezone.utc),
                )
            )
            return result.rowcount > 0


# ── DB Audit Log ────────────────────────────────────────────────


class DbAuditLog:
    """Audit log backed by the ``audit_log`` table."""

    @staticmethod
    def _coerce_action(value: str) -> AuditAction:
        """Deserialize a stored action string tolerantly.

        A row with an action string not in the :class:`AuditAction` enum
        (e.g. a hand-inserted operator entry from before the value was
        added) must never crash reads — ``record()`` reads the newest row
        on every write to chain the hash, so one bad row would otherwise
        503 every publish/push. Unknown values fall back to
        ``admin_settings_update`` for in-memory use; the stored row is
        left untouched.
        """
        try:
            return AuditAction(value)
        except ValueError:
            return AuditAction.admin_settings_update

    @staticmethod
    def _entry_hash(entry: AuditEntry) -> str:
        payload = json.dumps(
            {
                "id": entry.id,
                "timestamp": entry.timestamp.isoformat(),
                "action": entry.action.value,
                "actor": entry.actor,
                "target": entry.target,
                "detail": entry.detail,
                "prev_sha256": entry.prev_sha256,
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    async def record(
        self,
        action: AuditAction,
        actor: str,
        target: str,
        detail: str = "",
    ) -> AuditEntry:
        async with get_session() as session:
            # Get prev hash from last entry
            result = await session.execute(select(AuditRow).order_by(AuditRow.id.desc()).limit(1))
            last = result.scalars().first()
            prev_hash = ""
            if last is not None:
                prev_entry = AuditEntry(
                    id=last.id,
                    timestamp=last.timestamp,
                    action=self._coerce_action(last.action),
                    actor=last.actor,
                    target=last.target,
                    detail=last.detail,
                    prev_sha256=last.prev_sha256,
                )
                prev_hash = self._entry_hash(prev_entry)

            row = AuditRow(
                action=action.value,
                actor=actor,
                target=target,
                detail=detail,
                prev_sha256=prev_hash,
            )
            session.add(row)
            await session.flush()

            return AuditEntry(
                id=row.id,
                timestamp=row.timestamp,
                action=action,
                actor=actor,
                target=target,
                detail=detail,
                prev_sha256=prev_hash,
            )

    async def entries(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        action: AuditAction | None = None,
        target: str = "",
    ) -> tuple[list[AuditEntry], int]:
        async with get_session() as session:
            q = select(AuditRow)
            count_q = select(sa_func.count(AuditRow.id))
            if action is not None:
                q = q.where(AuditRow.action == action.value)
                count_q = count_q.where(AuditRow.action == action.value)
            if target:
                q = q.where(AuditRow.target == target)
                count_q = count_q.where(AuditRow.target == target)

            total_result = await session.execute(count_q)
            total = total_result.scalar() or 0

            q = q.order_by(AuditRow.id).offset(offset).limit(limit)
            result = await session.execute(q)
            entries = [
                AuditEntry(
                    id=row.id,
                    timestamp=row.timestamp,
                    action=self._coerce_action(row.action),
                    actor=row.actor,
                    target=row.target,
                    detail=row.detail,
                    prev_sha256=row.prev_sha256,
                )
                for row in result.scalars().all()
            ]
            return entries, total

    async def verify_chain(self) -> tuple[bool, str]:
        async with get_session() as session:
            result = await session.execute(select(AuditRow).order_by(AuditRow.id))
            rows = result.scalars().all()

        if not rows:
            return True, "empty log"

        entries = [
            AuditEntry(
                id=r.id,
                timestamp=r.timestamp,
                action=self._coerce_action(r.action),
                actor=r.actor,
                target=r.target,
                detail=r.detail,
                prev_sha256=r.prev_sha256,
            )
            for r in rows
        ]

        if entries[0].prev_sha256:
            return False, "first entry has non-empty prev_sha256"

        for i in range(1, len(entries)):
            expected = self._entry_hash(entries[i - 1])
            if entries[i].prev_sha256 != expected:
                return (
                    False,
                    f"chain broken at entry {entries[i].id}: "
                    f"expected {expected}, got {entries[i].prev_sha256}",
                )
        return True, f"chain intact ({len(entries)} entries)"


# ── DB Package Index ────────────────────────────────────────────


class DbPackageIndex:
    """Package catalog backed by the ``packages`` table."""

    async def get_bundles(
        self,
        *,
        name: str = "",
        platform: str = "",
        release: str = "",
        search: str = "",
        org_slug: str = "",
        include_yanked: bool = False,
        limit: int = 1000,
        offset: int = 0,
        recipe_version: str = "",
        arch: str = "",
        build_type: str = "",
        link: str = "",
        caller_token_name: str | None = None,
    ) -> tuple[list[PackageInfo], int]:
        async with get_session() as session:
            q = select(PackageRow, TokenRow.email.label("publisher_email")).outerjoin(
                TokenRow, PackageRow.published_by == TokenRow.name
            )
            count_q = select(sa_func.count(PackageRow.id))
            if not include_yanked:
                q = q.where(PackageRow.yanked == False)  # noqa: E712
                count_q = count_q.where(PackageRow.yanked == False)  # noqa: E712
            if name:
                q = q.where(PackageRow.name == name)
                count_q = count_q.where(PackageRow.name == name)
            if platform:
                q = q.where(PackageRow.platform == platform)
                count_q = count_q.where(PackageRow.platform == platform)
            if release:
                q = q.where(PackageRow.release_tag == release)
                count_q = count_q.where(PackageRow.release_tag == release)
            if search:
                like_pat = f"%{search}%"
                search_filter = or_(
                    PackageRow.name.ilike(like_pat),
                    PackageRow.version.ilike(like_pat),
                    PackageRow.platform.ilike(like_pat),
                    PackageRow.arch.ilike(like_pat),
                    PackageRow.build_type.ilike(like_pat),
                    PackageRow.link.ilike(like_pat),
                    PackageRow.description.ilike(like_pat),
                    PackageRow.tags.ilike(like_pat),
                    PackageRow.maintainer.ilike(like_pat),
                    PackageRow.license.ilike(like_pat),
                    PackageRow.release_tag.ilike(like_pat),
                    PackageRow.org_slug.ilike(like_pat),
                )
                q = q.where(search_filter)
                count_q = count_q.where(search_filter)
            if org_slug:
                q = q.where(PackageRow.org_slug == org_slug)
                count_q = count_q.where(PackageRow.org_slug == org_slug)
            if caller_token_name is not None:
                # Visibility filter (opt-in): public base packages, packages in
                # public orgs, and — for a named caller — packages in private
                # orgs the caller is a member of.  Callers that pass None (the
                # default) get no filtering (internal/admin paths).
                visible = or_(
                    PackageRow.org_slug == "",
                    PackageRow.org_slug.in_(
                        select(OrganizationRow.slug).where(
                            OrganizationRow.is_private == False  # noqa: E712
                        )
                    ),
                )
                if caller_token_name:
                    member_orgs = (
                        select(OrganizationRow.slug)
                        .join(OrgMemberRow, OrgMemberRow.org_id == OrganizationRow.id)
                        .where(OrgMemberRow.token_name == caller_token_name)
                    )
                    visible = or_(visible, PackageRow.org_slug.in_(member_orgs))
                q = q.where(visible)
                count_q = count_q.where(visible)
            if recipe_version:
                q = q.where(PackageRow.recipe_version == recipe_version)
                count_q = count_q.where(PackageRow.recipe_version == recipe_version)
            if arch:
                q = q.where(PackageRow.arch == arch)
                count_q = count_q.where(PackageRow.arch == arch)
            if build_type:
                q = q.where(PackageRow.build_type == build_type)
                count_q = count_q.where(PackageRow.build_type == build_type)
            if link:
                q = q.where(PackageRow.link == link)
                count_q = count_q.where(PackageRow.link == link)

            total_result = await session.execute(count_q)
            total = total_result.scalar() or 0

            q = (
                q.order_by(PackageRow.name, PackageRow.published_at.desc())
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(q)
            packages = []
            for row_tuple in result.all():
                row = row_tuple[0]
                pub_email = row_tuple[1] or ""
                packages.append(
                    PackageInfo(
                        name=row.name,
                        version=row.version,
                        platform=row.platform,
                        arch=row.arch,
                        build_type=row.build_type,
                        link=row.link,
                        sha256=row.sha256,
                        size_bytes=row.size_bytes,
                        archive_url=row.archive_url,
                        published_at=row.published_at,
                        yanked=row.yanked,
                        signature=row.signature,
                        key_fingerprint=row.key_fingerprint,
                        release_tag=row.release_tag,
                        recipe_version=row.recipe_version,
                        description=row.description,
                        homepage=row.homepage,
                        license=row.license,
                        maintainer=row.maintainer,
                        tags=row.tags,
                        published_by=row.published_by,
                        published_by_email=pub_email,
                        org=row.org_slug,
                        required_deps=json.loads(row.required_deps or "[]"),
                    )
                )
            return packages, total

    async def get_search_facets(
        self,
        *,
        name: str = "",
        platform: str = "",
        release: str = "",
        search: str = "",
        org_slug: str = "",
        include_yanked: bool = False,
        recipe_version: str = "",
        arch: str = "",
        build_type: str = "",
        link: str = "",
        max_buckets: int = 50,
    ) -> tuple[dict[str, list[tuple[str, int]]], int, int, int]:
        """Return facet buckets for the same filter set as ``get_bundles``.

        Returns ``(facets, total_bundles, distinct_package_names,
        total_size_bytes)`` where ``facets`` maps the facet field name to a
        list of ``(value, count)`` tuples ordered by descending count.
        ``max_buckets`` caps the per-facet list length.
        """
        base_filters = []
        if not include_yanked:
            base_filters.append(PackageRow.yanked == False)  # noqa: E712
        if name:
            base_filters.append(PackageRow.name == name)
        if platform:
            base_filters.append(PackageRow.platform == platform)
        if release:
            base_filters.append(PackageRow.release_tag == release)
        if search:
            like_pat = f"%{search}%"
            base_filters.append(
                or_(
                    PackageRow.name.ilike(like_pat),
                    PackageRow.version.ilike(like_pat),
                    PackageRow.platform.ilike(like_pat),
                    PackageRow.arch.ilike(like_pat),
                    PackageRow.build_type.ilike(like_pat),
                    PackageRow.link.ilike(like_pat),
                    PackageRow.description.ilike(like_pat),
                    PackageRow.tags.ilike(like_pat),
                    PackageRow.maintainer.ilike(like_pat),
                    PackageRow.license.ilike(like_pat),
                    PackageRow.release_tag.ilike(like_pat),
                    PackageRow.org_slug.ilike(like_pat),
                )
            )
        if org_slug:
            base_filters.append(PackageRow.org_slug == org_slug)
        if recipe_version:
            base_filters.append(PackageRow.recipe_version == recipe_version)
        if arch:
            base_filters.append(PackageRow.arch == arch)
        if build_type:
            base_filters.append(PackageRow.build_type == build_type)
        if link:
            base_filters.append(PackageRow.link == link)

        facet_cols = {
            "platforms": PackageRow.platform,
            "archs": PackageRow.arch,
            "build_types": PackageRow.build_type,
            "links": PackageRow.link,
            "releases": PackageRow.release_tag,
            "orgs": PackageRow.org_slug,
            "licenses": PackageRow.license,
        }

        async with get_session() as session:
            total_q = select(sa_func.count(PackageRow.id))
            for f in base_filters:
                total_q = total_q.where(f)
            total_res = await session.execute(total_q)
            total_bundles = int(total_res.scalar() or 0)

            distinct_names_q = select(sa_func.count(distinct(PackageRow.name)))
            for f in base_filters:
                distinct_names_q = distinct_names_q.where(f)
            distinct_res = await session.execute(distinct_names_q)
            distinct_names = int(distinct_res.scalar() or 0)

            size_q = select(sa_func.coalesce(sa_func.sum(PackageRow.size_bytes), 0))
            for f in base_filters:
                size_q = size_q.where(f)
            size_res = await session.execute(size_q)
            total_size = int(size_res.scalar() or 0)

            facets: dict[str, list[tuple[str, int]]] = {}
            # Facet counts are number of distinct package names per bucket, not
            # bundle counts — a package built for many arch/link/build_type
            # combos should still count as one within a platform bucket.
            name_count = sa_func.count(distinct(PackageRow.name))
            for key, col in facet_cols.items():
                q = select(col, name_count)
                for f in base_filters:
                    q = q.where(f)
                q = q.group_by(col).order_by(name_count.desc(), col.asc()).limit(max_buckets)
                rows = await session.execute(q)
                facets[key] = [
                    (str(val or ""), int(cnt)) for val, cnt in rows.all() if (val or "") != ""
                ]

            # Tags is a comma-separated string column; aggregate in Python.
            # Count distinct package names per tag, not bundle rows.
            tag_q = select(PackageRow.name, PackageRow.tags)
            for f in base_filters:
                tag_q = tag_q.where(f)
            tag_rows = await session.execute(tag_q)
            tag_names: dict[str, set[str]] = {}
            for pkg_name, raw in tag_rows.all():
                if not raw:
                    continue
                for tag in raw.split(","):
                    t = tag.strip()
                    if not t:
                        continue
                    tag_names.setdefault(t, set()).add(pkg_name)
            tag_counts = {t: len(names) for t, names in tag_names.items()}
            facets["tags"] = sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))[
                :max_buckets
            ]

            return facets, total_bundles, distinct_names, total_size

    async def get_catalog_dict(
        self,
        *,
        caller_token_name: str = "",
        is_admin: bool = False,
    ) -> dict:
        """Return the full catalog as a dict (for /v1/catalog YAML response).

        Includes base packages (no org) and packages belonging to public
        organisations.  When *caller_token_name* is supplied, packages from
        private orgs where the caller is a member are also included.
        Admins (``is_admin=True``) see everything.
        """
        async with get_session() as session:
            # Left-join so base packages (org_slug == "") still appear even
            # when there is no matching OrganizationRow.
            q = (
                select(PackageRow)
                .outerjoin(
                    OrganizationRow,
                    PackageRow.org_slug == OrganizationRow.slug,
                )
                .where(PackageRow.yanked == False)  # noqa: E712
            )

            if is_admin:
                # Admins see all packages.
                pass
            elif caller_token_name:
                # Include private-org packages the caller is a member of.
                member_slugs_q = (
                    select(OrganizationRow.slug)
                    .join(OrgMemberRow, OrgMemberRow.org_id == OrganizationRow.id)
                    .where(OrgMemberRow.token_name == caller_token_name)
                    .where(OrganizationRow.is_private == True)  # noqa: E712
                )
                q = q.where(
                    or_(
                        PackageRow.org_slug == "",
                        OrganizationRow.is_private == False,  # noqa: E712
                        PackageRow.org_slug.in_(member_slugs_q),
                    )
                )
            else:
                # Anonymous: base packages + public orgs only.
                q = q.where(
                    or_(
                        PackageRow.org_slug == "",
                        OrganizationRow.is_private == False,  # noqa: E712
                    )
                )

            q = q.order_by(PackageRow.name)
            result = await session.execute(q)
            bundles = [
                {
                    "name": row.name,
                    "version": row.version,
                    "platform": row.platform,
                    "arch": row.arch,
                    "build_type": row.build_type,
                    "link": row.link,
                    "sha256": row.sha256,
                    "size_bytes": row.size_bytes,
                    "archive_url": row.archive_url,
                    "published_at": row.published_at.isoformat(),
                    "yanked": False,
                    "signature": row.signature,
                    "key_fingerprint": row.key_fingerprint,
                    "release_tag": row.release_tag,
                    "recipe_version": row.recipe_version,
                    "description": row.description,
                    "homepage": row.homepage,
                    "license": row.license,
                    "maintainer": row.maintainer,
                    "tags": row.tags,
                    "published_by": row.published_by,
                    "org": row.org_slug,
                    "required_deps": json.loads(row.required_deps or "[]"),
                }
                for row in result.scalars().all()
            ]
            count_result = await session.execute(select(sa_func.count(PackageRow.id)))
            revision = count_result.scalar() or 0
            return {"schema_version": 1, "revision": revision, "bundles": bundles}

    async def check_duplicate(
        self, name: str, version: str, platform: str, arch: str, build_type: str, link: str
    ) -> bool:
        async with get_session() as session:
            result = await session.execute(
                select(PackageRow).where(
                    PackageRow.name == name,
                    PackageRow.version == version,
                    PackageRow.platform == platform,
                    PackageRow.arch == arch,
                    PackageRow.build_type == build_type,
                    PackageRow.link == link,
                )
            )
            return result.scalars().first() is not None

    async def add_package(
        self,
        *,
        name: str,
        version: str,
        platform: str,
        arch: str,
        build_type: str,
        link: str,
        sha256: str,
        size_bytes: int,
        archive_url: str,
        signature: str = "",
        key_fingerprint: str = "",
        release_tag: str = "",
        recipe_version: str = "",
        description: str = "",
        homepage: str = "",
        pkg_license: str = "",
        maintainer: str = "",
        tags: str = "",
        org_slug: str = "",
        published_by: str = "",
        required_deps: str = "[]",
    ) -> None:
        async with get_session() as session:
            # Check for existing variant first.
            existing = await session.execute(
                select(PackageRow).where(
                    PackageRow.name == name,
                    PackageRow.version == version,
                    PackageRow.platform == platform,
                    PackageRow.arch == arch,
                    PackageRow.build_type == build_type,
                    PackageRow.link == link,
                    PackageRow.org_slug == org_slug,
                )
            )
            if existing.scalars().first() is not None:
                raise ValueError(
                    f"package {name}=={version} ({platform}/{arch}/{build_type}/{link})"
                    f" already exists" + (f" in org '{org_slug}'" if org_slug else "")
                )
            row = PackageRow(
                name=name,
                version=version,
                platform=platform,
                arch=arch,
                build_type=build_type,
                link=link,
                sha256=sha256,
                size_bytes=size_bytes,
                archive_url=archive_url,
                signature=signature,
                key_fingerprint=key_fingerprint,
                release_tag=release_tag,
                recipe_version=recipe_version,
                description=description,
                homepage=homepage,
                license=pkg_license,
                maintainer=maintainer,
                tags=tags,
                org_slug=org_slug,
                published_by=published_by,
                required_deps=required_deps,
            )
            session.add(row)

    async def yank(
        self,
        name: str,
        version: str,
        *,
        platform: str | None = None,
        arch: str | None = None,
        link: str | None = None,
        build_type: str | None = None,
    ) -> int:
        async with get_session() as session:
            stmt = (
                update(PackageRow)
                .where(PackageRow.name == name, PackageRow.version == version)
                .values(yanked=True)
            )
            if platform is not None:
                stmt = stmt.where(PackageRow.platform == platform)
            if arch is not None:
                stmt = stmt.where(PackageRow.arch == arch)
            if link is not None:
                stmt = stmt.where(PackageRow.link == link)
            if build_type is not None:
                stmt = stmt.where(PackageRow.build_type == build_type)
            result = await session.execute(stmt)
            return result.rowcount

    async def unyank(
        self,
        name: str,
        version: str,
        *,
        platform: str | None = None,
        arch: str | None = None,
        link: str | None = None,
        build_type: str | None = None,
    ) -> int:
        async with get_session() as session:
            stmt = (
                update(PackageRow)
                .where(PackageRow.name == name, PackageRow.version == version)
                .values(yanked=False)
            )
            if platform is not None:
                stmt = stmt.where(PackageRow.platform == platform)
            if arch is not None:
                stmt = stmt.where(PackageRow.arch == arch)
            if link is not None:
                stmt = stmt.where(PackageRow.link == link)
            if build_type is not None:
                stmt = stmt.where(PackageRow.build_type == build_type)
            result = await session.execute(stmt)
            return result.rowcount

    async def delete(
        self,
        name: str,
        version: str,
        *,
        platform: str | None = None,
        link: str | None = None,
    ) -> int:
        from sqlalchemy import delete as sa_delete

        async with get_session() as session:
            stmt = sa_delete(PackageRow).where(
                PackageRow.name == name, PackageRow.version == version
            )
            if platform is not None:
                stmt = stmt.where(PackageRow.platform == platform)
            if link is not None:
                stmt = stmt.where(PackageRow.link == link)
            result = await session.execute(stmt)
            return result.rowcount

    async def get_release_tags(self) -> list[dict]:
        """Distinct release tags with variant counts, most-populated first.

        The empty tag (live / untagged packages) is included as ``""``.
        """
        async with get_session() as session:
            q = (
                select(
                    PackageRow.release_tag,
                    sa_func.count(PackageRow.id).label("count"),
                )
                .group_by(PackageRow.release_tag)
                .order_by(sa_func.count(PackageRow.id).desc())
            )
            rows = (await session.execute(q)).all()
        return [{"tag": r.release_tag or "", "count": r.count} for r in rows]

    async def delete_by_link(self, platform: str, link: str) -> int:
        """Delete all bundles matching a platform and link mode."""
        from sqlalchemy import delete as sa_delete

        async with get_session() as session:
            result = await session.execute(
                sa_delete(PackageRow).where(
                    PackageRow.platform == platform,
                    PackageRow.link == link,
                )
            )
            return result.rowcount

    async def total_storage_bytes(self) -> int:
        """Return the total size_bytes across all non-yanked packages."""
        async with get_session() as session:
            result = await session.execute(
                select(sa_func.coalesce(sa_func.sum(PackageRow.size_bytes), 0)).where(
                    PackageRow.yanked.is_(False)
                )
            )
            return int(result.scalar_one())

    async def cache_stats(self) -> dict:
        """Return storage statistics: total, per-org, and package count."""
        async with get_session() as session:
            # Total non-yanked
            total_q = await session.execute(
                select(
                    sa_func.count(PackageRow.id),
                    sa_func.coalesce(sa_func.sum(PackageRow.size_bytes), 0),
                ).where(PackageRow.yanked.is_(False))
            )
            total_count, total_bytes = total_q.one()

            # Per-org breakdown
            org_q = await session.execute(
                select(
                    PackageRow.org_slug,
                    sa_func.count(PackageRow.id),
                    sa_func.coalesce(sa_func.sum(PackageRow.size_bytes), 0),
                )
                .where(PackageRow.yanked.is_(False))
                .group_by(PackageRow.org_slug)
            )
            orgs = {row[0] or "": {"count": row[1], "size_bytes": row[2]} for row in org_q.all()}

            return {
                "total_packages": int(total_count),
                "total_size_bytes": int(total_bytes),
                "orgs": orgs,
            }

    async def gc_by_age(self, max_age_seconds: float) -> list[dict]:
        """Delete non-yanked packages older than *max_age_seconds*.

        Returns a list of dicts with ``name``, ``version``, ``size_bytes``,
        ``org_slug`` for each deleted package.
        """
        import datetime as _dt

        from sqlalchemy import delete as sa_delete

        cutoff = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(seconds=max_age_seconds)
        async with get_session() as session:
            # Fetch rows to delete (for the return value).
            rows = (
                (
                    await session.execute(
                        select(PackageRow).where(
                            PackageRow.published_at < cutoff,
                            PackageRow.yanked.is_(False),
                            PackageRow.release_tag == "",
                        )
                    )
                )
                .scalars()
                .all()
            )
            deleted = [
                {
                    "name": r.name,
                    "version": r.version,
                    "size_bytes": r.size_bytes,
                    "org_slug": r.org_slug,
                }
                for r in rows
            ]
            if rows:
                ids = [r.id for r in rows]
                await session.execute(sa_delete(PackageRow).where(PackageRow.id.in_(ids)))
            return deleted

    async def gc_by_storage(self, max_bytes: int) -> list[dict]:
        """Evict oldest non-release packages until total storage <= *max_bytes*.

        Returns list of deleted package info dicts.
        """
        from sqlalchemy import delete as sa_delete

        async with get_session() as session:
            total_q = await session.execute(
                select(sa_func.coalesce(sa_func.sum(PackageRow.size_bytes), 0)).where(
                    PackageRow.yanked.is_(False)
                )
            )
            current = int(total_q.scalar_one())
            if current <= max_bytes:
                return []

            # Fetch non-release packages ordered by oldest first.
            candidates = (
                (
                    await session.execute(
                        select(PackageRow)
                        .where(
                            PackageRow.yanked.is_(False),
                            PackageRow.release_tag == "",
                        )
                        .order_by(PackageRow.published_at.asc())
                    )
                )
                .scalars()
                .all()
            )

            to_delete: list[dict] = []
            ids_to_delete: list[int] = []
            for row in candidates:
                if current <= max_bytes:
                    break
                current -= row.size_bytes
                to_delete.append(
                    {
                        "name": row.name,
                        "version": row.version,
                        "size_bytes": row.size_bytes,
                        "org_slug": row.org_slug,
                    }
                )
                ids_to_delete.append(row.id)

            if ids_to_delete:
                await session.execute(sa_delete(PackageRow).where(PackageRow.id.in_(ids_to_delete)))
            return to_delete

    async def gc_by_staleness(self, valid_chain_hashes: set[str]) -> list[dict]:
        """Delete non-release packages whose recipe_version is not in *valid_chain_hashes*.

        Returns list of deleted package info dicts.
        """
        from sqlalchemy import delete as sa_delete

        async with get_session() as session:
            # Fetch non-release, non-yanked packages.
            rows = (
                (
                    await session.execute(
                        select(PackageRow).where(
                            PackageRow.yanked.is_(False),
                            PackageRow.release_tag == "",
                            PackageRow.recipe_version != "",
                        )
                    )
                )
                .scalars()
                .all()
            )

            stale = [r for r in rows if r.recipe_version not in valid_chain_hashes]
            deleted = [
                {
                    "name": r.name,
                    "version": r.version,
                    "size_bytes": r.size_bytes,
                    "org_slug": r.org_slug,
                }
                for r in stale
            ]
            if stale:
                ids = [r.id for r in stale]
                await session.execute(sa_delete(PackageRow).where(PackageRow.id.in_(ids)))
            return deleted


# ── DB Organization Store ───────────────────────────────────────


class DbOrgStore:
    """Organization management backed by the ``organizations`` and ``org_members`` tables."""

    async def create(
        self,
        *,
        slug: str,
        display_name: str,
        description: str = "",
        logo_url: str = "",
        homepage: str = "",
        is_private: bool = False,
        created_by: str,
        storage_limit_bytes: int = 10 * 1024 * 1024 * 1024,
    ) -> OrgInfo:
        async with get_session() as session:
            existing = await session.execute(
                select(OrganizationRow).where(OrganizationRow.slug == slug)
            )
            if existing.scalars().first() is not None:
                raise ValueError(f"organization '{slug}' already exists")

            row = OrganizationRow(
                slug=slug,
                display_name=display_name,
                description=description,
                logo_url=logo_url,
                homepage=homepage,
                is_private=is_private,
                created_by=created_by,
                storage_limit_bytes=storage_limit_bytes,
            )
            session.add(row)
            await session.flush()

            # Creator is automatically the owner
            member = OrgMemberRow(
                org_id=row.id,
                token_name=created_by,
                role=OrgRole.owner.value,
            )
            session.add(member)

            return self._row_to_info(row)

    async def get(self, slug: str) -> OrgInfo | None:
        async with get_session() as session:
            result = await session.execute(
                select(OrganizationRow).where(OrganizationRow.slug == slug)
            )
            row = result.scalars().first()
            if row is None:
                return None
            return self._row_to_info(row)

    async def list_orgs(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        include_private: bool = False,
    ) -> tuple[list[OrgInfo], int]:
        async with get_session() as session:
            base_filter = (
                True if include_private else OrganizationRow.is_private == False  # noqa: E712
            )
            count_q = select(sa_func.count(OrganizationRow.id)).where(base_filter)
            total = (await session.execute(count_q)).scalar() or 0

            q = (
                select(OrganizationRow)
                .where(base_filter)
                .order_by(OrganizationRow.display_name)
                .offset(offset)
                .limit(limit)
            )
            result = await session.execute(q)
            return [self._row_to_info(r) for r in result.scalars().all()], total

    async def update(
        self,
        slug: str,
        *,
        display_name: str | None = None,
        description: str | None = None,
        logo_url: str | None = None,
        homepage: str | None = None,
        is_private: bool | None = None,
        storage_limit_bytes: int | None = None,
    ) -> OrgInfo | None:
        async with get_session() as session:
            result = await session.execute(
                select(OrganizationRow).where(OrganizationRow.slug == slug)
            )
            row = result.scalars().first()
            if row is None:
                return None
            if display_name is not None:
                row.display_name = display_name
            if description is not None:
                row.description = description
            if logo_url is not None:
                row.logo_url = logo_url
            if homepage is not None:
                row.homepage = homepage
            if is_private is not None:
                row.is_private = is_private
            if storage_limit_bytes is not None:
                row.storage_limit_bytes = storage_limit_bytes
            await session.flush()
            return self._row_to_info(row)

    async def add_member(self, slug: str, token_name: str, role: OrgRole = OrgRole.member) -> bool:
        async with get_session() as session:
            org = (
                (await session.execute(select(OrganizationRow).where(OrganizationRow.slug == slug)))
                .scalars()
                .first()
            )
            if org is None:
                raise ValueError(f"organization '{slug}' not found")

            existing = (
                (
                    await session.execute(
                        select(OrgMemberRow).where(
                            OrgMemberRow.org_id == org.id,
                            OrgMemberRow.token_name == token_name,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if existing is not None:
                return False  # already a member

            session.add(
                OrgMemberRow(
                    org_id=org.id,
                    token_name=token_name,
                    role=role.value,
                )
            )
            return True

    async def remove_member(self, slug: str, token_name: str) -> bool:
        from sqlalchemy import delete as sa_delete

        async with get_session() as session:
            org = (
                (await session.execute(select(OrganizationRow).where(OrganizationRow.slug == slug)))
                .scalars()
                .first()
            )
            if org is None:
                raise ValueError(f"organization '{slug}' not found")

            result = await session.execute(
                sa_delete(OrgMemberRow).where(
                    OrgMemberRow.org_id == org.id,
                    OrgMemberRow.token_name == token_name,
                )
            )
            return result.rowcount > 0

    async def get_members(self, slug: str) -> list[OrgMember]:
        async with get_session() as session:
            org = (
                (await session.execute(select(OrganizationRow).where(OrganizationRow.slug == slug)))
                .scalars()
                .first()
            )
            if org is None:
                return []

            result = await session.execute(
                select(OrgMemberRow)
                .where(OrgMemberRow.org_id == org.id)
                .order_by(OrgMemberRow.added_at)
            )
            return [
                OrgMember(
                    token_name=m.token_name,
                    role=OrgRole(m.role),
                    added_at=m.added_at,
                )
                for m in result.scalars().all()
            ]

    async def is_member(self, slug: str, token_name: str) -> bool:
        async with get_session() as session:
            org = (
                (await session.execute(select(OrganizationRow).where(OrganizationRow.slug == slug)))
                .scalars()
                .first()
            )
            if org is None:
                return False
            result = await session.execute(
                select(OrgMemberRow).where(
                    OrgMemberRow.org_id == org.id,
                    OrgMemberRow.token_name == token_name,
                )
            )
            return result.scalars().first() is not None

    async def is_owner(self, slug: str, token_name: str) -> bool:
        async with get_session() as session:
            org = (
                (await session.execute(select(OrganizationRow).where(OrganizationRow.slug == slug)))
                .scalars()
                .first()
            )
            if org is None:
                return False
            result = await session.execute(
                select(OrgMemberRow).where(
                    OrgMemberRow.org_id == org.id,
                    OrgMemberRow.token_name == token_name,
                    OrgMemberRow.role == OrgRole.owner.value,
                )
            )
            return result.scalars().first() is not None

    async def update_storage_used(self, slug: str, delta_bytes: int) -> None:
        async with get_session() as session:
            org = (
                (await session.execute(select(OrganizationRow).where(OrganizationRow.slug == slug)))
                .scalars()
                .first()
            )
            if org is not None:
                org.storage_used_bytes = max(0, org.storage_used_bytes + delta_bytes)
                await session.flush()

    async def check_storage_limit(self, slug: str, additional_bytes: int) -> bool:
        """Return True if adding additional_bytes would stay within the org's limit."""
        async with get_session() as session:
            org = (
                (await session.execute(select(OrganizationRow).where(OrganizationRow.slug == slug)))
                .scalars()
                .first()
            )
            if org is None:
                return False
            return (org.storage_used_bytes + additional_bytes) <= org.storage_limit_bytes

    @staticmethod
    def _row_to_info(row: OrganizationRow) -> OrgInfo:
        return OrgInfo(
            slug=row.slug,
            display_name=row.display_name,
            description=row.description,
            logo_url=row.logo_url,
            homepage=row.homepage,
            is_private=row.is_private,
            storage_limit_bytes=row.storage_limit_bytes,
            storage_used_bytes=row.storage_used_bytes,
            created_at=row.created_at,
            created_by=row.created_by,
        )


# ── DB Download Store ───────────────────────────────────────────


class DbDownloadStore:
    """Download event tracking backed by the ``download_events`` table."""

    async def record(
        self,
        package_name: str,
        version: str,
        platform: str = "",
        *,
        arch: str = "",
        client_ip_hash: str = "",
        user_agent: str = "",
        cvcpkg_version: str = "",
        bytes_sent: int = 0,
    ) -> None:
        """Record a download event.

        ``client_ip_hash`` must already be salted+hashed by the caller —
        this store never sees a plain client address.
        """
        async with get_session() as session:
            row = DownloadEventRow(
                package_name=package_name,
                version=version,
                platform=platform,
                arch=arch,
                client_ip_hash=client_ip_hash,
                user_agent=user_agent[:255],
                cvcpkg_version=cvcpkg_version[:64],
                bytes_sent=bytes_sent,
            )
            session.add(row)

    async def get_total_downloads(self, package_name: str = "") -> int:
        """Get total download count, optionally filtered by package name."""
        async with get_session() as session:
            q = select(sa_func.count(DownloadEventRow.id))
            if package_name:
                q = q.where(DownloadEventRow.package_name == package_name)
            result = await session.execute(q)
            return result.scalar() or 0

    @staticmethod
    def _day_bucket():
        """Dialect-safe "date part of downloaded_at" expression.

        ``CAST(... AS DATE)`` is correct on PostgreSQL/MySQL but broken on
        SQLite, where DATE has NUMERIC affinity — ``CAST('2026-07-14 ...' AS
        DATE)`` yields the integer ``2026``, which then blows up SQLAlchemy's
        Date result processor.  SQLite's ``date()`` function returns the
        proper ``YYYY-MM-DD`` string instead.
        """
        from sqlalchemy import Date, cast

        from cvcpkg.server import db as _db

        dialect = ""
        if getattr(_db, "_engine", None) is not None:
            dialect = _db._engine.dialect.name
        if dialect == "sqlite":
            return sa_func.date(DownloadEventRow.downloaded_at)
        return cast(DownloadEventRow.downloaded_at, Date)

    async def get_daily_downloads(
        self,
        package_name: str = "",
        days: int = 30,
    ) -> list[dict]:
        """Get daily download counts for the last N days.

        Returns list of {"date": "YYYY-MM-DD", "count": N} dicts.
        """
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        async with get_session() as session:
            q = select(
                self._day_bucket().label("day"),
                sa_func.count(DownloadEventRow.id).label("count"),
            ).where(DownloadEventRow.downloaded_at >= cutoff)
            if package_name:
                q = q.where(DownloadEventRow.package_name == package_name)
            q = q.group_by("day").order_by("day")
            result = await session.execute(q)
            rows = result.all()

        # Fill in missing days with zero counts
        day_counts: dict[str, int] = {}
        for row in rows:
            day_str = row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day)
            day_counts[day_str] = row.count

        result_list = []
        for i in range(days):
            d = (cutoff + datetime.timedelta(days=i + 1)).date()
            ds = d.isoformat()
            result_list.append({"date": ds, "count": day_counts.get(ds, 0)})
        return result_list

    async def get_top_packages(self, days: int = 30, limit: int = 20) -> list[dict]:
        """Top downloaded packages over the last N days.

        Returns [{"name": ..., "count": N, "bytes_sent": M}, ...] ordered by
        count descending.
        """
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        async with get_session() as session:
            q = (
                select(
                    DownloadEventRow.package_name,
                    sa_func.count(DownloadEventRow.id).label("count"),
                    sa_func.coalesce(sa_func.sum(DownloadEventRow.bytes_sent), 0).label("bytes"),
                )
                .where(DownloadEventRow.downloaded_at >= cutoff)
                .group_by(DownloadEventRow.package_name)
                .order_by(sa_func.count(DownloadEventRow.id).desc())
                .limit(limit)
            )
            rows = (await session.execute(q)).all()
        return [
            {"name": r.package_name, "count": r.count, "bytes_sent": int(r.bytes or 0)}
            for r in rows
        ]

    async def get_platform_distribution(self, days: int = 30) -> list[dict]:
        """Download counts grouped by (platform, arch) over the last N days."""
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        async with get_session() as session:
            q = (
                select(
                    DownloadEventRow.platform,
                    DownloadEventRow.arch,
                    sa_func.count(DownloadEventRow.id).label("count"),
                )
                .where(DownloadEventRow.downloaded_at >= cutoff)
                .group_by(DownloadEventRow.platform, DownloadEventRow.arch)
                .order_by(sa_func.count(DownloadEventRow.id).desc())
            )
            rows = (await session.execute(q)).all()
        return [
            {"platform": r.platform or "unknown", "arch": r.arch or "", "count": r.count}
            for r in rows
        ]

    async def get_bandwidth(self, package_name: str = "", days: int = 30) -> dict:
        """Bandwidth accounting over the last N days.

        Returns {"total_bytes": N, "daily": [{"date", "bytes"}, ...]} with
        zero-filled days, oldest first.
        """
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        async with get_session() as session:
            q = select(
                self._day_bucket().label("day"),
                sa_func.coalesce(sa_func.sum(DownloadEventRow.bytes_sent), 0).label("bytes"),
            ).where(DownloadEventRow.downloaded_at >= cutoff)
            if package_name:
                q = q.where(DownloadEventRow.package_name == package_name)
            q = q.group_by("day").order_by("day")
            rows = (await session.execute(q)).all()

        day_bytes: dict[str, int] = {}
        for row in rows:
            day_str = row.day.isoformat() if hasattr(row.day, "isoformat") else str(row.day)
            day_bytes[day_str] = int(row.bytes or 0)

        daily = []
        total = 0
        for i in range(days):
            d = (cutoff + datetime.timedelta(days=i + 1)).date()
            ds = d.isoformat()
            b = day_bytes.get(ds, 0)
            total += b
            daily.append({"date": ds, "bytes": b})
        return {"total_bytes": total, "daily": daily}

    async def get_client_versions(self, days: int = 30) -> list[dict]:
        """cvcpkg client version distribution over the last N days.

        Events without a client version (browsers, curl) are aggregated
        under the empty string.
        """
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        async with get_session() as session:
            q = (
                select(
                    DownloadEventRow.cvcpkg_version,
                    sa_func.count(DownloadEventRow.id).label("count"),
                )
                .where(DownloadEventRow.downloaded_at >= cutoff)
                .group_by(DownloadEventRow.cvcpkg_version)
                .order_by(sa_func.count(DownloadEventRow.id).desc())
            )
            rows = (await session.execute(q)).all()
        return [{"version": r.cvcpkg_version or "", "count": r.count} for r in rows]


# ── Telemetry store ─────────────────────────────────────────────


class DbTelemetryStore:
    """Opt-in client telemetry backed by ``telemetry_events``.

    Aggregate-only by design: the row carries no address, hostname, or
    user identifier, so every query here is a GROUP BY over environment
    facts.
    """

    async def record(
        self,
        *,
        platform: str = "",
        arch: str = "",
        python_version: str = "",
        cvcpkg_version: str = "",
        ci: bool = False,
        tools: dict[str, str] | None = None,
    ) -> None:
        """Store one telemetry ping."""
        tools_json = json.dumps(
            {str(k)[:32]: str(v)[:64] for k, v in (tools or {}).items()},
            sort_keys=True,
        )[:2048]
        async with get_session() as session:
            session.add(
                TelemetryEventRow(
                    platform=platform[:64],
                    arch=arch[:64],
                    python_version=python_version[:64],
                    cvcpkg_version=cvcpkg_version[:64],
                    ci=bool(ci),
                    tools=tools_json,
                )
            )

    async def get_summary(self, days: int = 30) -> dict:
        """Aggregate telemetry over the last N days."""
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        async with get_session() as session:
            total = (
                await session.execute(
                    select(sa_func.count(TelemetryEventRow.id)).where(
                        TelemetryEventRow.received_at >= cutoff
                    )
                )
            ).scalar() or 0

            async def _grouped(*cols):
                q = (
                    select(*cols, sa_func.count(TelemetryEventRow.id).label("count"))
                    .where(TelemetryEventRow.received_at >= cutoff)
                    .group_by(*cols)
                    .order_by(sa_func.count(TelemetryEventRow.id).desc())
                )
                return (await session.execute(q)).all()

            plat_rows = await _grouped(TelemetryEventRow.platform, TelemetryEventRow.arch)
            py_rows = await _grouped(TelemetryEventRow.python_version)
            ver_rows = await _grouped(TelemetryEventRow.cvcpkg_version)
            ci_rows = await _grouped(TelemetryEventRow.ci)

        return {
            "total": total,
            "platforms": [
                {"platform": r.platform or "unknown", "arch": r.arch or "", "count": r.count}
                for r in plat_rows
            ],
            "python_versions": [
                {"version": r.python_version or "", "count": r.count} for r in py_rows
            ],
            "cvcpkg_versions": [
                {"version": r.cvcpkg_version or "", "count": r.count} for r in ver_rows
            ],
            "ci": [{"ci": bool(r.ci), "count": r.count} for r in ci_rows],
        }


# ── Mirror store ────────────────────────────────────────────────


class DbMirrorStore:
    """DB-backed store for registered mirror servers."""

    @staticmethod
    def _row_to_info(row: MirrorRow) -> MirrorInfo:
        return MirrorInfo(
            url=row.url,
            display_name=row.display_name,
            contact=row.contact,
            registered_at=row.registered_at,
            last_health_check=row.last_health_check,
            last_healthy_at=row.last_healthy_at,
            healthy=row.healthy,
            consecutive_failures=row.consecutive_failures,
            rejected=row.rejected,
            rejected_at=row.rejected_at,
            rejected_by=row.rejected_by,
            packages_count=row.packages_count,
        )

    async def register(
        self,
        url: str,
        display_name: str = "",
        contact: str = "",
    ) -> MirrorInfo:
        """Register a new mirror or update an existing one."""
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            row = (await session.execute(select(MirrorRow).where(MirrorRow.url == url))).scalar()
            if row is not None:
                # Re-registration: update metadata, clear rejection if set
                row.display_name = display_name or row.display_name
                row.contact = contact or row.contact
                row.rejected = False
                row.rejected_at = None
                row.rejected_by = ""
                row.healthy = True
                row.consecutive_failures = 0
                return self._row_to_info(row)
            row = MirrorRow(
                url=url,
                display_name=display_name,
                contact=contact,
                registered_at=now,
                healthy=True,
            )
            session.add(row)
            await session.flush()
            return self._row_to_info(row)

    async def list_healthy(self) -> list[MirrorInfo]:
        """Return all healthy, non-rejected mirrors."""
        async with get_session() as session:
            q = (
                select(MirrorRow)
                .where(MirrorRow.rejected == False)  # noqa: E712
                .where(MirrorRow.healthy == True)  # noqa: E712
                .order_by(MirrorRow.registered_at)
            )
            rows = (await session.execute(q)).scalars().all()
            return [self._row_to_info(r) for r in rows]

    async def list_all(self) -> list[MirrorInfo]:
        """Return all mirrors including rejected and unhealthy."""
        async with get_session() as session:
            rows = (
                (await session.execute(select(MirrorRow).order_by(MirrorRow.registered_at)))
                .scalars()
                .all()
            )
            return [self._row_to_info(r) for r in rows]

    async def get(self, url: str) -> MirrorInfo | None:
        """Get a single mirror by URL."""
        async with get_session() as session:
            row = (await session.execute(select(MirrorRow).where(MirrorRow.url == url))).scalar()
            return self._row_to_info(row) if row else None

    async def reject(self, url: str, actor: str) -> bool:
        """Mark a mirror as rejected. Returns True if found."""
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            row = (await session.execute(select(MirrorRow).where(MirrorRow.url == url))).scalar()
            if row is None:
                return False
            row.rejected = True
            row.rejected_at = now
            row.rejected_by = actor
            return True

    async def remove(self, url: str) -> bool:
        """Delete a mirror entirely. Returns True if found."""
        from sqlalchemy import delete

        async with get_session() as session:
            result = await session.execute(delete(MirrorRow).where(MirrorRow.url == url))
            return result.rowcount > 0

    async def record_health_check(self, url: str, *, healthy: bool) -> MirrorInfo | None:
        """Record a health check result. Returns updated info or None."""
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            row = (await session.execute(select(MirrorRow).where(MirrorRow.url == url))).scalar()
            if row is None:
                return None
            row.last_health_check = now
            if healthy:
                row.healthy = True
                row.last_healthy_at = now
                row.consecutive_failures = 0
            else:
                row.consecutive_failures += 1
                if row.consecutive_failures >= 3:
                    row.healthy = False
            return self._row_to_info(row)

    async def update_packages_count(self, url: str, count: int) -> None:
        """Update the cached package count for a mirror."""
        async with get_session() as session:
            row = (await session.execute(select(MirrorRow).where(MirrorRow.url == url))).scalar()
            if row is not None:
                row.packages_count = count


# ── DB Tag Store ────────────────────────────────────────────────


class DbTagStore:
    """CRUD for curated tag metadata backed by the ``tags`` table."""

    @staticmethod
    def _row_to_info(row: TagRow, package_count: int = 0) -> TagInfo:
        return TagInfo(
            name=row.name,
            org_slug=row.org_slug,
            display_name=row.display_name,
            description=row.description,
            logo_url=row.logo_url,
            package_count=package_count,
            created_at=row.created_at,
            updated_at=row.updated_at,
            created_by=row.created_by,
        )

    async def create(
        self,
        *,
        name: str,
        org_slug: str = "",
        display_name: str = "",
        description: str = "",
        logo_url: str = "",
        created_by: str = "",
    ) -> TagInfo:
        async with get_session() as session:
            existing = (
                await session.execute(
                    select(TagRow).where(TagRow.name == name, TagRow.org_slug == org_slug)
                )
            ).scalar()
            if existing is not None:
                raise ValueError(
                    f"tag '{org_slug}/{name}' already exists"
                    if org_slug
                    else f"tag '{name}' already exists"
                )
            row = TagRow(
                name=name,
                org_slug=org_slug,
                display_name=display_name or name,
                description=description,
                logo_url=logo_url,
                created_by=created_by,
            )
            session.add(row)
            await session.flush()
            return self._row_to_info(row)

    async def update(
        self,
        *,
        name: str,
        org_slug: str = "",
        display_name: str | None = None,
        description: str | None = None,
        logo_url: str | None = None,
    ) -> TagInfo | None:
        async with get_session() as session:
            row = (
                await session.execute(
                    select(TagRow).where(TagRow.name == name, TagRow.org_slug == org_slug)
                )
            ).scalar()
            if row is None:
                return None
            if display_name is not None:
                row.display_name = display_name
            if description is not None:
                row.description = description
            if logo_url is not None:
                row.logo_url = logo_url
            await session.flush()
            return self._row_to_info(row)

    async def ensure_tags(
        self,
        *,
        tags_csv: str,
        org_slug: str = "",
        created_by: str = "",
    ) -> None:
        """Create stub tag rows for any tags not already in the table.

        Called during publish so that ad-hoc tags are immediately
        browsable.  Admins can curate display_name / description /
        logo later.
        """
        if not tags_csv:
            return
        names = [t.strip().lower() for t in tags_csv.split(",") if t.strip()]
        if not names:
            return
        async with get_session() as session:
            for tag_name in names:
                existing = (
                    await session.execute(
                        select(TagRow).where(
                            TagRow.name == tag_name,
                            TagRow.org_slug == org_slug,
                        )
                    )
                ).scalar()
                if existing is None:
                    session.add(
                        TagRow(
                            name=tag_name,
                            org_slug=org_slug,
                            display_name=tag_name,
                            created_by=created_by,
                        )
                    )
            await session.flush()

    async def delete(self, *, name: str, org_slug: str = "") -> bool:
        from sqlalchemy import delete as sa_delete

        async with get_session() as session:
            result = await session.execute(
                sa_delete(TagRow).where(TagRow.name == name, TagRow.org_slug == org_slug)
            )
            return result.rowcount > 0

    async def get(self, *, name: str, org_slug: str = "") -> TagInfo | None:
        async with get_session() as session:
            row = (
                await session.execute(
                    select(TagRow).where(TagRow.name == name, TagRow.org_slug == org_slug)
                )
            ).scalar()
            if row is None:
                return None
            count = await self._count_packages(session, name, org_slug)
            return self._row_to_info(row, package_count=count)

    async def list_tags(
        self,
        *,
        org_slug: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> tuple[list[TagInfo], int]:
        async with get_session() as session:
            q = select(TagRow)
            count_q = select(sa_func.count(TagRow.id))
            if org_slug is not None:
                q = q.where(TagRow.org_slug == org_slug)
                count_q = count_q.where(TagRow.org_slug == org_slug)
            total = (await session.execute(count_q)).scalar() or 0
            rows = (
                (
                    await session.execute(
                        q.order_by(TagRow.org_slug, TagRow.name).offset(offset).limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            result: list[TagInfo] = []
            for row in rows:
                count = await self._count_packages(session, row.name, row.org_slug)
                result.append(self._row_to_info(row, package_count=count))
            return result, total

    async def list_all_tag_names(self) -> list[dict]:
        """Return lightweight tag summaries including package counts.

        This collects both curated tags from the ``tags`` table *and*
        ad-hoc tags found in published packages that have no curated
        row yet, so the front page shows all tags in use.
        """
        async with get_session() as session:
            # 1. Curated tags
            curated = (
                (await session.execute(select(TagRow).order_by(TagRow.org_slug, TagRow.name)))
                .scalars()
                .all()
            )
            result: dict[str, dict] = {}
            for row in curated:
                count = await self._count_packages(session, row.name, row.org_slug)
                key = f"{row.org_slug}/{row.name}" if row.org_slug else row.name
                result[key] = {
                    "name": row.name,
                    "org_slug": row.org_slug,
                    "display_name": row.display_name,
                    "description": row.description,
                    "logo_url": row.logo_url,
                    "package_count": count,
                }

            # 2. Ad-hoc tags from packages not yet curated
            curated_keys = {f"{r.org_slug}/{r.name}" if r.org_slug else r.name for r in curated}
            all_tags_rows = (
                await session.execute(
                    select(PackageRow.name, PackageRow.tags, PackageRow.org_slug)
                    .where(PackageRow.tags != "")
                    .where(PackageRow.yanked == False)  # noqa: E712
                )
            ).all()
            # Track distinct package names per ad-hoc tag
            adhoc_names: dict[str, set[str]] = {}
            for pkg_name, tags_str, org in all_tags_rows:
                for raw_tag in tags_str.split(","):
                    tag = raw_tag.strip().lower()
                    if not tag:
                        continue
                    key = f"{org}/{tag}" if org else tag
                    if key not in result:
                        result[key] = {
                            "name": tag,
                            "org_slug": org,
                            "display_name": tag,
                            "description": "",
                            "logo_url": "",
                            "package_count": 0,
                        }
                    if key not in curated_keys:
                        adhoc_names.setdefault(key, set()).add(pkg_name)
            for key, names in adhoc_names.items():
                result[key]["package_count"] = len(names)

            return sorted(result.values(), key=lambda t: t["name"])

    @staticmethod
    async def _count_packages(session, tag_name: str, org_slug: str) -> int:
        """Count distinct non-yanked package *names* whose tags contain *tag_name*."""
        like_pat = f"%{tag_name}%"
        q = select(sa_func.count(distinct(PackageRow.name))).where(
            PackageRow.tags.ilike(like_pat),
            PackageRow.yanked == False,  # noqa: E712
        )
        if org_slug:
            q = q.where(PackageRow.org_slug == org_slug)
        return (await session.execute(q)).scalar() or 0


# ── DB Builder Store ────────────────────────────────────────────


class DbBuilderStore:
    """DB-backed store for registered remote build agents."""

    @staticmethod
    def _row_to_info(row: BuilderRow) -> BuilderInfo:
        labels_raw = row.labels or "[]"
        caps_raw = row.capabilities or "{}"
        try:
            labels = json.loads(labels_raw)
        except (json.JSONDecodeError, TypeError):
            labels = []
        try:
            capabilities = json.loads(caps_raw)
        except (json.JSONDecodeError, TypeError):
            capabilities = {}
        return BuilderInfo(
            id=row.id,
            name=row.name,
            org_slug=row.org_slug,
            platform=row.platform,
            arch=row.arch,
            labels=labels,
            capabilities=capabilities,
            status=row.status,
            current_jobs=row.current_jobs,
            max_jobs=row.max_jobs,
            prefer_affinity=row.prefer_affinity,
            last_heartbeat=row.last_heartbeat,
            registered_by=row.registered_by,
            created_at=row.created_at,
        )

    async def register(
        self,
        name: str,
        platform: str,
        arch: str,
        registered_by: str,
        *,
        org_slug: str = "",
        labels: list[str] | None = None,
        capabilities: dict | None = None,
        max_jobs: int = 1,
        prefer_affinity: bool = False,
    ) -> BuilderInfo:
        """Register a new builder or re-register an existing one."""
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            row = (
                await session.execute(
                    select(BuilderRow).where(
                        BuilderRow.name == name,
                        BuilderRow.org_slug == org_slug,
                    )
                )
            ).scalar()
            if row is not None:
                row.platform = platform
                row.arch = arch
                row.labels = json.dumps(labels or [])
                row.capabilities = json.dumps(capabilities or {})
                row.max_jobs = max_jobs
                row.prefer_affinity = prefer_affinity
                row.status = BuilderStatus.online
                row.last_heartbeat = now
                row.registered_by = registered_by
                return self._row_to_info(row)
            row = BuilderRow(
                name=name,
                org_slug=org_slug,
                platform=platform,
                arch=arch,
                labels=json.dumps(labels or []),
                capabilities=json.dumps(capabilities or {}),
                status=BuilderStatus.online,
                current_jobs=0,
                max_jobs=max_jobs,
                prefer_affinity=prefer_affinity,
                last_heartbeat=now,
                registered_by=registered_by,
            )
            session.add(row)
            await session.flush()
            return self._row_to_info(row)

    async def get(self, builder_id: int) -> BuilderInfo | None:
        """Get a builder by ID."""
        async with get_session() as session:
            row = (
                await session.execute(select(BuilderRow).where(BuilderRow.id == builder_id))
            ).scalar()
            return self._row_to_info(row) if row else None

    async def list_builders(
        self,
        *,
        org_slug: str | None = None,
        platform: str | None = None,
        arch: str | None = None,
        status: str | None = None,
    ) -> list[BuilderInfo]:
        """List builders with optional filters."""
        async with get_session() as session:
            q = select(BuilderRow).order_by(BuilderRow.id)
            if org_slug is not None:
                q = q.where(BuilderRow.org_slug == org_slug)
            if platform is not None:
                q = q.where(BuilderRow.platform == platform)
            if arch is not None:
                q = q.where(BuilderRow.arch == arch)
            if status is not None:
                q = q.where(BuilderRow.status == status)
            rows = (await session.execute(q)).scalars().all()
            return [self._row_to_info(r) for r in rows]

    async def update(
        self,
        builder_id: int,
        *,
        labels: list[str] | None = None,
        capabilities: dict | None = None,
        max_jobs: int | None = None,
        prefer_affinity: bool | None = None,
    ) -> BuilderInfo | None:
        """Update mutable fields. Returns updated info or None if not found."""
        async with get_session() as session:
            row = (
                await session.execute(select(BuilderRow).where(BuilderRow.id == builder_id))
            ).scalar()
            if row is None:
                return None
            if labels is not None:
                row.labels = json.dumps(labels)
            if capabilities is not None:
                row.capabilities = json.dumps(capabilities)
            if max_jobs is not None:
                row.max_jobs = max_jobs
            if prefer_affinity is not None:
                row.prefer_affinity = prefer_affinity
            return self._row_to_info(row)

    async def heartbeat(
        self,
        builder_id: int,
        *,
        status: str = BuilderStatus.online,
        current_jobs: int = 0,
        reconcile: bool = False,
    ) -> BuilderInfo | None:
        """Record a heartbeat from a builder. Returns updated info or None.

        When *reconcile* is True, ``current_jobs`` is computed from the
        actual number of dispatched/running jobs in the database rather
        than trusting the client-reported value.  This prevents the
        counter from drifting after builder restarts or lost heartbeats.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            row = (
                await session.execute(select(BuilderRow).where(BuilderRow.id == builder_id))
            ).scalar()
            if row is None:
                return None
            row.last_heartbeat = now
            row.status = status
            if reconcile:
                db_count = (
                    await session.execute(
                        select(sa_func.count())
                        .select_from(BuildJobRow)
                        .where(BuildJobRow.builder_id == builder_id)
                        .where(
                            BuildJobRow.status.in_(
                                [BuildJobStatus.dispatched, BuildJobStatus.running]
                            )
                        )
                    )
                ).scalar() or 0
                row.current_jobs = db_count
            else:
                row.current_jobs = current_jobs
            return self._row_to_info(row)

    async def unregister(self, builder_id: int) -> bool:
        """Remove a builder. Returns True if found and deleted."""
        from sqlalchemy import delete

        async with get_session() as session:
            result = await session.execute(delete(BuilderRow).where(BuilderRow.id == builder_id))
            return result.rowcount > 0

    async def reap_stale(self, max_age_seconds: int = 180) -> list[BuilderInfo]:
        """Mark builders as offline if their last heartbeat exceeds max_age_seconds.

        Returns the list of builders that were reaped.
        """
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            seconds=max_age_seconds
        )
        async with get_session() as session:
            q = (
                select(BuilderRow)
                .where(BuilderRow.status != BuilderStatus.offline)
                .where(
                    or_(
                        BuilderRow.last_heartbeat < cutoff,
                        BuilderRow.last_heartbeat.is_(None),
                    )
                )
            )
            rows = (await session.execute(q)).scalars().all()
            reaped = []
            for row in rows:
                row.status = BuilderStatus.offline
                row.current_jobs = 0
                reaped.append(self._row_to_info(row))
            return reaped


# ── DB Build Job Store ──────────────────────────────────────────


class DbBuildJobStore:
    """DB-backed store for build job queue and DAG scheduling."""

    @staticmethod
    def _row_to_info(row: BuildJobRow, dep_ids: list[int] | None = None) -> BuildJobInfo:
        return BuildJobInfo(
            id=row.id,
            dag_id=row.dag_id,
            org_slug=row.org_slug,
            recipe_name=row.recipe_name,
            recipe_version=row.recipe_version,
            recipe_hash=row.recipe_hash,
            platform=row.platform,
            arch=row.arch,
            config=row.config,
            link=row.link,
            builder_id=row.builder_id,
            status=row.status,
            priority=row.priority,
            timeout_seconds=row.timeout_seconds,
            submitted_by=row.submitted_by,
            submitted_at=row.submitted_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            log_url=row.log_url,
            log_size_bytes=row.log_size_bytes,
            error_message=row.error_message,
            result_archive_url=row.result_archive_url,
            depends_on=dep_ids if dep_ids is not None else [],
        )

    async def _load_dep_ids(self, session, job_id: int) -> list[int]:
        """Load prerequisite job IDs for a given job."""
        q = select(BuildJobDepRow.depends_on_job_id).where(BuildJobDepRow.job_id == job_id)
        rows = (await session.execute(q)).scalars().all()
        return list(rows)

    async def create(
        self,
        recipe_name: str,
        platform: str,
        arch: str,
        submitted_by: str,
        *,
        recipe_version: str = "",
        recipe_hash: str = "",
        config: str = "release",
        link: str = "shared",
        org_slug: str = "",
        dag_id: str | None = None,
        priority: int = 0,
        timeout_seconds: int | None = None,
        depends_on: list[int] | None = None,
    ) -> BuildJobInfo:
        """Create a new build job."""
        async with get_session() as session:
            row = BuildJobRow(
                dag_id=dag_id,
                org_slug=org_slug,
                recipe_name=recipe_name,
                recipe_version=recipe_version,
                recipe_hash=recipe_hash,
                platform=platform,
                arch=arch,
                config=config,
                link=link,
                status=BuildJobStatus.pending,
                priority=priority,
                timeout_seconds=timeout_seconds,
                submitted_by=submitted_by,
            )
            session.add(row)
            await session.flush()

            dep_ids = []
            if depends_on:
                for dep_id in depends_on:
                    dep_row = BuildJobDepRow(job_id=row.id, depends_on_job_id=dep_id)
                    session.add(dep_row)
                dep_ids = list(depends_on)

            return self._row_to_info(row, dep_ids)

    async def create_dag(
        self,
        jobs: list[dict],
        dag_id: str,
        submitted_by: str,
    ) -> list[BuildJobInfo]:
        """Create multiple jobs as a DAG in a single transaction.

        Each dict in *jobs* should contain keys matching
        ``BuildJobSubmitRequest`` fields.  ``depends_on`` values are
        indices (0-based) into the *jobs* list (resolved to real IDs
        after insertion).
        """
        async with get_session() as session:
            created_rows: list[BuildJobRow] = []
            idx_deps: list[list[int]] = []  # index-based deps per job

            for job in jobs:
                row = BuildJobRow(
                    dag_id=dag_id,
                    org_slug=job.get("org_slug", ""),
                    recipe_name=job["recipe_name"],
                    recipe_version=job.get("recipe_version", ""),
                    recipe_hash=job.get("recipe_hash", ""),
                    platform=job["platform"],
                    arch=job["arch"],
                    config=job.get("config", "release"),
                    link=job.get("link", "shared"),
                    status=BuildJobStatus.pending,
                    priority=job.get("priority", 0),
                    timeout_seconds=job.get("timeout_seconds"),
                    submitted_by=submitted_by,
                )
                session.add(row)
                created_rows.append(row)
                idx_deps.append(job.get("depends_on", []))

            await session.flush()  # assigns IDs

            # Resolve index-based deps to real IDs and insert edges.
            # Deduplicate to avoid violating the (job_id, depends_on_job_id)
            # unique constraint when callers include repeated indices.
            for i, row in enumerate(created_rows):
                seen: set[int] = set()
                for dep_idx in idx_deps[i]:
                    if 0 <= dep_idx < len(created_rows) and dep_idx not in seen:
                        seen.add(dep_idx)
                        dep_row = BuildJobDepRow(
                            job_id=row.id,
                            depends_on_job_id=created_rows[dep_idx].id,
                        )
                        session.add(dep_row)

            await session.flush()

            # Build results with dep IDs (deduplicated)
            results = []
            for i, row in enumerate(created_rows):
                real_dep_ids = list(
                    dict.fromkeys(
                        created_rows[di].id for di in idx_deps[i] if 0 <= di < len(created_rows)
                    )
                )
                results.append(self._row_to_info(row, real_dep_ids))
            return results

    async def get(self, job_id: int) -> BuildJobInfo | None:
        """Get a build job by ID."""
        async with get_session() as session:
            row = (
                await session.execute(select(BuildJobRow).where(BuildJobRow.id == job_id))
            ).scalar()
            if row is None:
                return None
            dep_ids = await self._load_dep_ids(session, job_id)
            return self._row_to_info(row, dep_ids)

    async def list_jobs(
        self,
        *,
        org_slug: str | None = None,
        dag_id: str | None = None,
        status: str | None = None,
        platform: str | None = None,
        arch: str | None = None,
        config: str | None = None,
        link: str | None = None,
        recipe_name: str | None = None,
        builder_id: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[BuildJobInfo], int]:
        """List jobs with filters. Returns (jobs, total_count)."""
        async with get_session() as session:
            q = select(BuildJobRow)
            count_q = select(sa_func.count(BuildJobRow.id))
            if org_slug is not None:
                q = q.where(BuildJobRow.org_slug == org_slug)
                count_q = count_q.where(BuildJobRow.org_slug == org_slug)
            if dag_id is not None:
                if dag_id.endswith("*"):
                    prefix = dag_id[:-1]
                    q = q.where(BuildJobRow.dag_id.startswith(prefix))
                    count_q = count_q.where(BuildJobRow.dag_id.startswith(prefix))
                else:
                    q = q.where(BuildJobRow.dag_id == dag_id)
                    count_q = count_q.where(BuildJobRow.dag_id == dag_id)
            if status is not None:
                q = q.where(BuildJobRow.status == status)
                count_q = count_q.where(BuildJobRow.status == status)
            if platform is not None:
                q = q.where(BuildJobRow.platform == platform)
                count_q = count_q.where(BuildJobRow.platform == platform)
            if arch is not None:
                q = q.where(BuildJobRow.arch == arch)
                count_q = count_q.where(BuildJobRow.arch == arch)
            if config is not None:
                q = q.where(BuildJobRow.config == config)
                count_q = count_q.where(BuildJobRow.config == config)
            if link is not None:
                q = q.where(BuildJobRow.link == link)
                count_q = count_q.where(BuildJobRow.link == link)
            if recipe_name is not None:
                q = q.where(BuildJobRow.recipe_name == recipe_name)
                count_q = count_q.where(BuildJobRow.recipe_name == recipe_name)
            if builder_id is not None:
                q = q.where(BuildJobRow.builder_id == builder_id)
                count_q = count_q.where(BuildJobRow.builder_id == builder_id)

            total = (await session.execute(count_q)).scalar() or 0
            q = (
                q.order_by(BuildJobRow.submitted_at.desc(), BuildJobRow.id.desc())
                .limit(limit)
                .offset(offset)
            )
            rows = (await session.execute(q)).scalars().all()
            results = []
            for row in rows:
                dep_ids = await self._load_dep_ids(session, row.id)
                results.append(self._row_to_info(row, dep_ids))
            return results, total

    async def cancel(self, job_id: int, *, force: bool = False) -> BuildJobInfo | None:
        """Cancel a job. Returns updated info or None.

        Without *force*, only pending/dispatched jobs transition to
        cancelled (running jobs are left untouched — this is the safe
        default that avoids racing with an in-flight builder).

        With *force=True*, dispatched/running jobs are also cancelled
        (and ``finished_at`` set). This is the recovery path when a
        builder has died or is stuck and no completion is coming.
        Callers using force=True should typically also invoke
        :meth:`cancel_downstream` to propagate the cancellation.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        allowed = {BuildJobStatus.pending, BuildJobStatus.dispatched}
        if force:
            allowed = allowed | {BuildJobStatus.running}
        async with get_session() as session:
            row = (
                await session.execute(select(BuildJobRow).where(BuildJobRow.id == job_id))
            ).scalar()
            if row is None:
                return None
            if row.status not in allowed:
                return self._row_to_info(row)
            builder_id = row.builder_id
            row.status = BuildJobStatus.cancelled
            row.finished_at = now
            if force and builder_id is not None:
                # Reconcile builder's current_jobs from actual DB state
                active = (
                    await session.execute(
                        select(sa_func.count())
                        .select_from(BuildJobRow)
                        .where(BuildJobRow.builder_id == builder_id)
                        .where(BuildJobRow.id != job_id)
                        .where(
                            BuildJobRow.status.in_(
                                [BuildJobStatus.dispatched, BuildJobStatus.running]
                            )
                        )
                    )
                ).scalar() or 0
                builder_row = (
                    await session.execute(select(BuilderRow).where(BuilderRow.id == builder_id))
                ).scalar()
                if builder_row is not None:
                    builder_row.current_jobs = active
            dep_ids = await self._load_dep_ids(session, job_id)
            return self._row_to_info(row, dep_ids)

    async def list_active_by_builder(self, builder_id: int) -> list[BuildJobInfo]:
        """Return jobs currently assigned to a builder in dispatched or running state."""
        async with get_session() as session:
            q = (
                select(BuildJobRow)
                .where(BuildJobRow.builder_id == builder_id)
                .where(BuildJobRow.status.in_([BuildJobStatus.dispatched, BuildJobStatus.running]))
            )
            rows = (await session.execute(q)).scalars().all()
            results = []
            for row in rows:
                dep_ids = await self._load_dep_ids(session, row.id)
                results.append(self._row_to_info(row, dep_ids))
            return results

    async def cancel_dag(self, dag_id: str) -> int:
        """Cancel all pending/dispatched jobs in a DAG. Returns count cancelled."""
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            q = (
                select(BuildJobRow)
                .where(BuildJobRow.dag_id == dag_id)
                .where(
                    BuildJobRow.status.in_(
                        [
                            BuildJobStatus.pending,
                            BuildJobStatus.dispatched,
                        ]
                    )
                )
            )
            rows = (await session.execute(q)).scalars().all()
            for row in rows:
                row.status = BuildJobStatus.cancelled
                row.finished_at = now
            return len(rows)

    async def pause(self, job_id: int) -> BuildJobInfo | None:
        """Pause a pending or dispatched job. Returns updated info or None."""
        async with get_session() as session:
            row = (
                await session.execute(select(BuildJobRow).where(BuildJobRow.id == job_id))
            ).scalar()
            if row is None:
                return None
            if row.status not in (
                BuildJobStatus.pending,
                BuildJobStatus.dispatched,
            ):
                return self._row_to_info(row)
            row.status = BuildJobStatus.paused
            dep_ids = await self._load_dep_ids(session, job_id)
            return self._row_to_info(row, dep_ids)

    async def resume(self, job_id: int) -> BuildJobInfo | None:
        """Resume a paused job back to pending. Returns updated info or None."""
        async with get_session() as session:
            row = (
                await session.execute(select(BuildJobRow).where(BuildJobRow.id == job_id))
            ).scalar()
            if row is None:
                return None
            if row.status != BuildJobStatus.paused:
                return self._row_to_info(row)
            row.status = BuildJobStatus.pending
            dep_ids = await self._load_dep_ids(session, job_id)
            return self._row_to_info(row, dep_ids)

    async def pause_dag(self, dag_id: str) -> int:
        """Pause all pending/dispatched jobs in a DAG. Returns count paused."""
        async with get_session() as session:
            q = (
                select(BuildJobRow)
                .where(BuildJobRow.dag_id == dag_id)
                .where(
                    BuildJobRow.status.in_(
                        [
                            BuildJobStatus.pending,
                            BuildJobStatus.dispatched,
                        ]
                    )
                )
            )
            rows = (await session.execute(q)).scalars().all()
            for row in rows:
                row.status = BuildJobStatus.paused
            return len(rows)

    async def resume_dag(self, dag_id: str) -> int:
        """Resume all paused jobs in a DAG back to pending. Returns count resumed."""
        async with get_session() as session:
            q = (
                select(BuildJobRow)
                .where(BuildJobRow.dag_id == dag_id)
                .where(BuildJobRow.status == BuildJobStatus.paused)
            )
            rows = (await session.execute(q)).scalars().all()
            for row in rows:
                row.status = BuildJobStatus.pending
            return len(rows)

    _TERMINAL_STATUSES = frozenset(
        {
            BuildJobStatus.succeeded,
            BuildJobStatus.failed,
            BuildJobStatus.cancelled,
            BuildJobStatus.timed_out,
            BuildJobStatus.unschedulable,
        }
    )

    async def is_dag_complete(self, dag_id: str) -> bool | None:
        """Return True if every job in the DAG is in a terminal state.

        Returns None if the DAG has no jobs.
        """
        async with get_session() as session:
            rows = (
                (
                    await session.execute(
                        select(BuildJobRow.status).where(BuildJobRow.dag_id == dag_id)
                    )
                )
                .scalars()
                .all()
            )
            if not rows:
                return None
            return all(s in self._TERMINAL_STATUSES for s in rows)

    async def dag_summary(self, dag_id: str) -> dict:
        """Return a status summary dict for a DAG."""
        async with get_session() as session:
            rows = (
                (await session.execute(select(BuildJobRow).where(BuildJobRow.dag_id == dag_id)))
                .scalars()
                .all()
            )
            total = len(rows)
            succeeded = sum(1 for r in rows if r.status == BuildJobStatus.succeeded)
            failed = sum(1 for r in rows if r.status == BuildJobStatus.failed)
            unschedulable = sum(1 for r in rows if r.status == BuildJobStatus.unschedulable)
            return {
                "dag_id": dag_id,
                "total": total,
                "succeeded": succeeded,
                "failed": failed,
                "unschedulable": unschedulable,
            }

    async def claim(self, job_id: int, builder_id: int) -> BuildJobInfo | None:
        """Builder claims a dispatched job → running."""
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            row = (
                await session.execute(select(BuildJobRow).where(BuildJobRow.id == job_id))
            ).scalar()
            if row is None:
                return None
            if row.status not in (
                BuildJobStatus.pending,
                BuildJobStatus.dispatched,
            ):
                dep_ids = await self._load_dep_ids(session, job_id)
                return self._row_to_info(row, dep_ids)
            row.status = BuildJobStatus.running
            row.builder_id = builder_id
            row.started_at = now
            dep_ids = await self._load_dep_ids(session, job_id)
            return self._row_to_info(row, dep_ids)

    async def complete(self, job_id: int, *, result_archive_url: str = "") -> BuildJobInfo | None:
        """Mark a job as succeeded and reconcile builder job count."""
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            row = (
                await session.execute(select(BuildJobRow).where(BuildJobRow.id == job_id))
            ).scalar()
            if row is None:
                return None
            builder_id = row.builder_id
            row.status = BuildJobStatus.succeeded
            row.finished_at = now
            row.error_message = ""
            if result_archive_url:
                row.result_archive_url = result_archive_url
            # Reconcile builder's current_jobs from actual DB state
            if builder_id is not None:
                active = (
                    await session.execute(
                        select(sa_func.count())
                        .select_from(BuildJobRow)
                        .where(BuildJobRow.builder_id == builder_id)
                        .where(
                            BuildJobRow.status.in_(
                                [BuildJobStatus.dispatched, BuildJobStatus.running]
                            )
                        )
                    )
                ).scalar() or 0
                builder_row = (
                    await session.execute(select(BuilderRow).where(BuilderRow.id == builder_id))
                ).scalar()
                if builder_row is not None:
                    builder_row.current_jobs = active
            dep_ids = await self._load_dep_ids(session, job_id)
            return self._row_to_info(row, dep_ids)

    async def fail(self, job_id: int, *, error_message: str = "") -> BuildJobInfo | None:
        """Mark a job as failed and reconcile builder job count."""
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            row = (
                await session.execute(select(BuildJobRow).where(BuildJobRow.id == job_id))
            ).scalar()
            if row is None:
                return None
            builder_id = row.builder_id
            row.status = BuildJobStatus.failed
            row.finished_at = now
            if error_message:
                row.error_message = error_message
            # Reconcile builder's current_jobs from actual DB state
            if builder_id is not None:
                active = (
                    await session.execute(
                        select(sa_func.count())
                        .select_from(BuildJobRow)
                        .where(BuildJobRow.builder_id == builder_id)
                        .where(
                            BuildJobRow.status.in_(
                                [BuildJobStatus.dispatched, BuildJobStatus.running]
                            )
                        )
                    )
                ).scalar() or 0
                builder_row = (
                    await session.execute(select(BuilderRow).where(BuilderRow.id == builder_id))
                ).scalar()
                if builder_row is not None:
                    builder_row.current_jobs = active
            dep_ids = await self._load_dep_ids(session, job_id)
            return self._row_to_info(row, dep_ids)

    async def find_ready_jobs(self) -> list[BuildJobInfo]:
        """Find pending jobs whose dependencies are all succeeded.

        Returns jobs ordered by priority (desc) then id (asc).
        """
        async with get_session() as session:
            # Subquery: jobs that have unmet dependencies
            # A dependency is "unmet" if the depended-on job is not succeeded
            unmet_sub = (
                select(BuildJobDepRow.job_id)
                .join(
                    BuildJobRow,
                    BuildJobRow.id == BuildJobDepRow.depends_on_job_id,
                )
                .where(BuildJobRow.status != BuildJobStatus.succeeded)
                .distinct()
            )

            q = (
                select(BuildJobRow)
                .where(BuildJobRow.status == BuildJobStatus.pending)
                .where(BuildJobRow.id.notin_(unmet_sub))
                .order_by(BuildJobRow.priority.desc(), BuildJobRow.id)
            )
            rows = (await session.execute(q)).scalars().all()
            results = []
            for row in rows:
                dep_ids = await self._load_dep_ids(session, row.id)
                results.append(self._row_to_info(row, dep_ids))
            return results

    async def dispatch(self, job_id: int, builder_id: int) -> BuildJobInfo | None:
        """Mark a pending job as dispatched to a specific builder."""
        async with get_session() as session:
            row = (
                await session.execute(select(BuildJobRow).where(BuildJobRow.id == job_id))
            ).scalar()
            if row is None:
                return None
            if row.status != BuildJobStatus.pending:
                dep_ids = await self._load_dep_ids(session, job_id)
                return self._row_to_info(row, dep_ids)
            row.status = BuildJobStatus.dispatched
            row.builder_id = builder_id
            dep_ids = await self._load_dep_ids(session, job_id)
            return self._row_to_info(row, dep_ids)

    async def reap_timed_out(self, default_timeout: int = 86400) -> list[BuildJobInfo]:
        """Mark running jobs that exceed their timeout as timed_out.

        Uses per-job ``timeout_seconds`` if set, otherwise *default_timeout*.

        The reference time is ``started_at`` when set, else ``submitted_at``.
        A ``running`` row is *supposed* to have ``started_at``, but a builder
        that abandons a job (crashed, killed mid-build, or a claim that set
        the status without a start timestamp) can leave one with
        ``started_at IS NULL``.  The old query excluded those, so such rows
        stayed ``running`` forever — and because the heartbeat reconciles a
        builder's ``current_jobs`` from its running jobs, two stuck rows
        pinned a max-jobs=2 builder at capacity and it silently stopped
        taking work (this wedged both openbsd builders).  Falling back to
        ``submitted_at`` (always set, and old for a genuinely stuck row)
        lets the reaper clear them.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            q = select(BuildJobRow).where(BuildJobRow.status == BuildJobStatus.running)
            rows = (await session.execute(q)).scalars().all()
            reaped = []
            for row in rows:
                timeout = row.timeout_seconds or default_timeout
                started = row.started_at or row.submitted_at
                if started is None:
                    continue
                # SQLite returns naive datetimes; ensure UTC-aware
                if started.tzinfo is None:
                    started = started.replace(tzinfo=datetime.timezone.utc)
                deadline = started + datetime.timedelta(seconds=timeout)
                if now > deadline:
                    row.status = BuildJobStatus.timed_out
                    row.finished_at = now
                    row.error_message = f"exceeded {timeout}s timeout"
                    dep_ids = await self._load_dep_ids(session, row.id)
                    reaped.append(self._row_to_info(row, dep_ids))
            return reaped

    async def reap_unschedulable(
        self,
        schedulable_targets: set[tuple[str, str]],
        schedulable_platforms: set[str],
        *,
        min_age_seconds: int,
    ) -> list[BuildJobInfo]:
        """Mark long-pending jobs that no registered builder can serve.

        A job is *unschedulable* when its (platform, arch) is covered by no
        registered builder — neither by an exact ``platform``/``arch`` match
        nor by a platform-only cross-build capability — and it has been
        pending for at least *min_age_seconds*.  Such jobs would otherwise
        sit in ``pending`` forever, since no builder will ever claim them.

        ``schedulable_targets`` is the set of exact ``(platform, arch)``
        pairs a registered builder advertises; ``schedulable_platforms`` is
        the set of platforms advertised by legacy platform-only cross-build
        targets (any arch).  Both are derived purely from builders
        registered with this server — the server has no other notion of
        what can be built.

        Returns the jobs that were reaped so the caller can cancel their
        downstream dependents and emit events.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        cutoff = now - datetime.timedelta(seconds=min_age_seconds)
        async with get_session() as session:
            q = select(BuildJobRow).where(
                BuildJobRow.status == BuildJobStatus.pending,
                BuildJobRow.submitted_at.isnot(None),
            )
            rows = (await session.execute(q)).scalars().all()
            reaped: list[BuildJobInfo] = []
            for row in rows:
                submitted = row.submitted_at
                # SQLite returns naive datetimes; ensure UTC-aware
                if submitted.tzinfo is None:
                    submitted = submitted.replace(tzinfo=datetime.timezone.utc)
                if submitted > cutoff:
                    continue  # still within the grace period
                if (row.platform, row.arch) in schedulable_targets:
                    continue
                if row.platform in schedulable_platforms:
                    continue
                row.status = BuildJobStatus.unschedulable
                row.finished_at = now
                row.error_message = f"no registered builder for {row.platform}/{row.arch}"
                dep_ids = await self._load_dep_ids(session, row.id)
                reaped.append(self._row_to_info(row, dep_ids))
            return reaped

    async def cancel_downstream(self, failed_job_id: int) -> int:
        """Cancel all pending/dispatched jobs that depend (transitively) on a failed job.

        Returns count of cancelled jobs.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            # BFS to find all downstream jobs
            to_visit = [failed_job_id]
            visited: set[int] = set()
            cancelled = 0

            while to_visit:
                current_id = to_visit.pop(0)
                if current_id in visited:
                    continue
                visited.add(current_id)

                # Find jobs that depend on current_id
                q = select(BuildJobDepRow.job_id).where(
                    BuildJobDepRow.depends_on_job_id == current_id
                )
                downstream_ids = (await session.execute(q)).scalars().all()

                for ds_id in downstream_ids:
                    if ds_id in visited:
                        continue
                    row = (
                        await session.execute(select(BuildJobRow).where(BuildJobRow.id == ds_id))
                    ).scalar()
                    if row and row.status in (
                        BuildJobStatus.pending,
                        BuildJobStatus.dispatched,
                    ):
                        row.status = BuildJobStatus.cancelled
                        row.finished_at = now
                        row.error_message = f"cancelled: dependency {failed_job_id} failed"
                        cancelled += 1
                    to_visit.append(ds_id)

            return cancelled

    # ── Log management ──────────────────────────────────────────

    async def append_log(
        self,
        job_id: int,
        data: str,
        *,
        logs_dir: Path,
    ) -> BuildJobInfo | None:
        """Append a chunk of log data to a job's log file.

        Creates the log file on first append.  Updates ``log_url`` and
        ``log_size_bytes`` on the DB row.
        """
        async with get_session() as session:
            row = (
                await session.execute(select(BuildJobRow).where(BuildJobRow.id == job_id))
            ).scalar()
            if row is None:
                return None

            # Determine log file path
            if row.dag_id:
                log_sub = logs_dir / row.dag_id
            else:
                log_sub = logs_dir / "standalone"
            log_sub.mkdir(parents=True, exist_ok=True)
            log_path = log_sub / f"{job_id}.log"

            # Append data
            with open(log_path, "ab") as f:
                f.write(data.encode() if isinstance(data, str) else data)

            # Update row metadata
            size = log_path.stat().st_size
            # Use forward slashes so the URL is consistent on Windows too.
            row.log_url = log_path.relative_to(logs_dir).as_posix()
            row.log_size_bytes = size

            dep_ids = await self._load_dep_ids(session, job_id)
            return self._row_to_info(row, dep_ids)

    async def get_log_path(
        self,
        job_id: int,
        *,
        logs_dir: Path,
    ) -> Path | None:
        """Return the filesystem path for a job's log, or None."""
        async with get_session() as session:
            row = (
                await session.execute(select(BuildJobRow).where(BuildJobRow.id == job_id))
            ).scalar()
            if row is None or not row.log_url:
                return None
            path = logs_dir / row.log_url
            if not path.is_file():
                return None
            return path

    async def delete_log(
        self,
        job_id: int,
        *,
        logs_dir: Path,
    ) -> bool:
        """Delete a job's log file.  Returns True if deleted."""
        async with get_session() as session:
            row = (
                await session.execute(select(BuildJobRow).where(BuildJobRow.id == job_id))
            ).scalar()
            if row is None:
                return False
            if row.log_url:
                path = logs_dir / row.log_url
                if path.is_file():
                    path.unlink()
            row.log_url = None
            row.log_size_bytes = None
            return True

    async def next_job_for_builder(self, builder_id: int) -> BuildJobInfo | None:
        """Find the next dispatched or pending job for a builder.

        Returns the first dispatched job assigned to this builder,
        or None.  Used by the long-poll ``next-job`` endpoint.
        """
        async with get_session() as session:
            q = (
                select(BuildJobRow)
                .where(
                    BuildJobRow.builder_id == builder_id,
                    BuildJobRow.status == BuildJobStatus.dispatched,
                )
                .order_by(BuildJobRow.priority.desc(), BuildJobRow.id.asc())
                .limit(1)
            )
            row = (await session.execute(q)).scalar()
            if row is None:
                return None
            dep_ids = await self._load_dep_ids(session, row.id)
            return self._row_to_info(row, dep_ids)

    async def purge_old_logs(
        self,
        *,
        older_than_days: int,
        logs_dir: Path,
        status_filter: str | None = None,
        delete_logs: bool = True,
    ) -> int:
        """Delete logs for finished jobs older than *older_than_days*.

        Returns the count of log entries purged.
        """
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=older_than_days
        )
        async with get_session() as session:
            q = select(BuildJobRow).where(
                BuildJobRow.log_url.isnot(None),
                BuildJobRow.finished_at.isnot(None),
                BuildJobRow.finished_at < cutoff,
            )
            if status_filter:
                q = q.where(BuildJobRow.status == status_filter)
            rows = (await session.execute(q)).scalars().all()
            count = 0
            for row in rows:
                if delete_logs and row.log_url:
                    path = logs_dir / row.log_url
                    if path.is_file():
                        path.unlink()
                row.log_url = None
                row.log_size_bytes = None
                count += 1
            return count

    async def get_org_log_usage(self, org_slug: str) -> int:
        """Return total log bytes for an org."""
        async with get_session() as session:
            result = (
                await session.execute(
                    select(sa_func.coalesce(sa_func.sum(BuildJobRow.log_size_bytes), 0)).where(
                        BuildJobRow.org_slug == org_slug
                    )
                )
            ).scalar()
            return int(result or 0)

    async def purge_old_jobs(
        self,
        *,
        older_than_days: int,
        logs_dir: Path,
        status_filter: str | None = None,
        delete_logs: bool = True,
    ) -> int:
        """Purge finished build jobs older than *older_than_days*.

        Deletes the job rows entirely (and their log files if
        *delete_logs* is True).  Returns the count of jobs purged.
        """
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=older_than_days
        )
        async with get_session() as session:
            q = select(BuildJobRow).where(
                BuildJobRow.finished_at.isnot(None),
                BuildJobRow.finished_at < cutoff,
            )
            if status_filter:
                q = q.where(BuildJobRow.status == status_filter)
            rows = (await session.execute(q)).scalars().all()
            count = 0
            for row in rows:
                if delete_logs and row.log_url:
                    path = logs_dir / row.log_url
                    if path.is_file():
                        path.unlink()
                # Delete dep rows
                from sqlalchemy import delete as sa_delete

                await session.execute(
                    sa_delete(BuildJobDepRow).where(
                        or_(
                            BuildJobDepRow.job_id == row.id,
                            BuildJobDepRow.depends_on_job_id == row.id,
                        )
                    )
                )
                await session.delete(row)
                count += 1
            return count


class DbRecipeStore:
    """DB-backed store for server-managed recipe bundles."""

    @staticmethod
    def _row_to_info(row: RecipeRow) -> RecipeInfo:
        return RecipeInfo(
            id=row.id,
            name=row.name,
            version=row.version,
            recipe_hash=row.recipe_hash,
            org_slug=row.org_slug,
            bundle_size=row.bundle_size,
            uploaded_by=row.uploaded_by,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def upload(
        self,
        name: str,
        bundle_path: str,
        bundle_size: int,
        uploaded_by: str,
        *,
        version: str = "",
        recipe_hash: str = "",
        org_slug: str = "",
    ) -> RecipeInfo:
        """Upload or update a recipe bundle.

        If a recipe with the same ``(name, org_slug)`` already exists,
        it is updated in place.  Returns the recipe info.
        """
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            q = select(RecipeRow).where(
                RecipeRow.name == name,
                RecipeRow.org_slug == org_slug,
            )
            row = (await session.execute(q)).scalar()
            if row is not None:
                row.version = version
                row.recipe_hash = recipe_hash
                row.bundle_path = bundle_path
                row.bundle_size = bundle_size
                row.uploaded_by = uploaded_by
                row.updated_at = now
            else:
                row = RecipeRow(
                    name=name,
                    version=version,
                    recipe_hash=recipe_hash,
                    org_slug=org_slug,
                    bundle_path=bundle_path,
                    bundle_size=bundle_size,
                    uploaded_by=uploaded_by,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                await session.flush()
            return self._row_to_info(row)

    async def get(self, name: str, *, org_slug: str = "") -> RecipeInfo | None:
        """Get a recipe by name (and org)."""
        async with get_session() as session:
            q = select(RecipeRow).where(
                RecipeRow.name == name,
                RecipeRow.org_slug == org_slug,
            )
            row = (await session.execute(q)).scalar()
            if row is None:
                return None
            return self._row_to_info(row)

    async def get_bundle_path(self, name: str, *, org_slug: str = "") -> str | None:
        """Return the bundle_path for a recipe, or None."""
        async with get_session() as session:
            q = select(RecipeRow.bundle_path).where(
                RecipeRow.name == name,
                RecipeRow.org_slug == org_slug,
            )
            result = (await session.execute(q)).scalar()
            return result

    async def list_recipes(
        self,
        *,
        org_slug: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[RecipeInfo], int]:
        """List recipes with optional org filter."""
        async with get_session() as session:
            q = select(RecipeRow)
            count_q = select(sa_func.count(RecipeRow.id))
            if org_slug is not None:
                q = q.where(RecipeRow.org_slug == org_slug)
                count_q = count_q.where(RecipeRow.org_slug == org_slug)
            total = (await session.execute(count_q)).scalar() or 0
            q = q.order_by(RecipeRow.name.asc()).limit(limit).offset(offset)
            rows = (await session.execute(q)).scalars().all()
            return [self._row_to_info(r) for r in rows], total

    async def delete(self, name: str, *, org_slug: str = "") -> bool:
        """Delete a recipe.  Returns True if a row was deleted."""
        async with get_session() as session:
            q = select(RecipeRow).where(
                RecipeRow.name == name,
                RecipeRow.org_slug == org_slug,
            )
            row = (await session.execute(q)).scalar()
            if row is None:
                return False
            await session.delete(row)
            return True


# ── DB Webhook Store ────────────────────────────────────────────


_AUTO_DISABLE_THRESHOLD = 5


class DbWebhookStore:
    """DB-backed store for webhook registrations."""

    @staticmethod
    def _row_to_info(row: WebhookRow) -> WebhookInfo:
        events_raw = row.events or "[]"
        try:
            events = json.loads(events_raw)
        except (json.JSONDecodeError, TypeError):
            events = []
        return WebhookInfo(
            id=row.id,
            url=row.url,
            events=events,
            org_slug=row.org_slug,
            active=row.active,
            registered_by=row.registered_by,
            created_at=row.created_at,
            last_delivery_at=row.last_delivery_at,
            consecutive_failures=row.consecutive_failures,
        )

    async def register(
        self,
        url: str,
        events: list[str],
        registered_by: str,
        *,
        org_slug: str = "",
        secret: str | None = None,
    ) -> WebhookInfo:
        """Register a new webhook."""
        if secret is None:
            secret = secrets.token_urlsafe(32)
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            row = WebhookRow(
                url=url,
                events=json.dumps(events),
                org_slug=org_slug,
                secret=secret,
                active=True,
                registered_by=registered_by,
                created_at=now,
                consecutive_failures=0,
            )
            session.add(row)
            await session.flush()
            return self._row_to_info(row)

    async def get(self, webhook_id: int) -> WebhookInfo | None:
        """Get a webhook by ID."""
        async with get_session() as session:
            row = (
                await session.execute(select(WebhookRow).where(WebhookRow.id == webhook_id))
            ).scalar()
            if row is None:
                return None
            return self._row_to_info(row)

    async def get_secret(self, webhook_id: int) -> str | None:
        """Return the secret for a webhook, or None."""
        async with get_session() as session:
            result = (
                await session.execute(select(WebhookRow.secret).where(WebhookRow.id == webhook_id))
            ).scalar()
            return result

    async def list_webhooks(
        self,
        *,
        org_slug: str | None = None,
        active_only: bool = False,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[WebhookInfo], int]:
        """List webhooks with optional filters."""
        async with get_session() as session:
            q = select(WebhookRow)
            count_q = select(sa_func.count(WebhookRow.id))
            if org_slug is not None:
                q = q.where(WebhookRow.org_slug == org_slug)
                count_q = count_q.where(WebhookRow.org_slug == org_slug)
            if active_only:
                q = q.where(WebhookRow.active.is_(True))
                count_q = count_q.where(WebhookRow.active.is_(True))
            total = (await session.execute(count_q)).scalar() or 0
            q = q.order_by(WebhookRow.id.asc()).limit(limit).offset(offset)
            rows = (await session.execute(q)).scalars().all()
            return [self._row_to_info(r) for r in rows], total

    async def update(
        self,
        webhook_id: int,
        *,
        url: str | None = None,
        events: list[str] | None = None,
        active: bool | None = None,
    ) -> WebhookInfo | None:
        """Update a webhook.  Returns updated info or None if not found."""
        async with get_session() as session:
            row = (
                await session.execute(select(WebhookRow).where(WebhookRow.id == webhook_id))
            ).scalar()
            if row is None:
                return None
            if url is not None:
                row.url = url
            if events is not None:
                row.events = json.dumps(events)
            if active is not None:
                row.active = active
            await session.flush()
            return self._row_to_info(row)

    async def delete(self, webhook_id: int) -> bool:
        """Delete a webhook.  Returns True if a row was deleted."""
        async with get_session() as session:
            row = (
                await session.execute(select(WebhookRow).where(WebhookRow.id == webhook_id))
            ).scalar()
            if row is None:
                return False
            await session.delete(row)
            return True

    async def record_delivery(self, webhook_id: int) -> None:
        """Record a successful delivery — reset consecutive failures."""
        now = datetime.datetime.now(datetime.timezone.utc)
        async with get_session() as session:
            await session.execute(
                update(WebhookRow)
                .where(WebhookRow.id == webhook_id)
                .values(
                    last_delivery_at=now,
                    consecutive_failures=0,
                )
            )

    async def record_failure(self, webhook_id: int) -> bool:
        """Record a delivery failure.

        Increments ``consecutive_failures``.  If the count reaches
        ``_AUTO_DISABLE_THRESHOLD`` the webhook is automatically
        deactivated.

        Returns True if the webhook was auto-disabled.
        """
        async with get_session() as session:
            row = (
                await session.execute(select(WebhookRow).where(WebhookRow.id == webhook_id))
            ).scalar()
            if row is None:
                return False
            row.consecutive_failures = row.consecutive_failures + 1
            disabled = row.consecutive_failures >= _AUTO_DISABLE_THRESHOLD
            if disabled:
                row.active = False
            await session.flush()
            return disabled

    async def list_active_for_event(
        self,
        event: str,
        *,
        org_slug: str = "",
    ) -> list[WebhookInfo]:
        """Return active webhooks subscribed to *event*."""
        async with get_session() as session:
            q = select(WebhookRow).where(
                WebhookRow.active.is_(True),
                WebhookRow.org_slug == org_slug,
            )
            rows = (await session.execute(q)).scalars().all()
            result: list[WebhookInfo] = []
            for r in rows:
                events_raw = r.events or "[]"
                try:
                    ev = json.loads(events_raw)
                except (json.JSONDecodeError, TypeError):
                    ev = []
                if event in ev or "*" in ev:
                    result.append(self._row_to_info(r))
            return result
