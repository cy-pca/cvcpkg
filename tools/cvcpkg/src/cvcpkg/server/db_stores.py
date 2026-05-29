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

from sqlalchemy import func as sa_func
from sqlalchemy import or_, select, update

from cvcpkg.server.db import (
    AuditRow,
    DownloadEventRow,
    MirrorRow,
    OrganizationRow,
    OrgMemberRow,
    PackageRow,
    TagRow,
    TokenRequestRow,
    TokenRow,
    get_session,
)
from cvcpkg.server.models import (
    AuditAction,
    AuditEntry,
    MirrorInfo,
    OrgInfo,
    OrgMember,
    OrgRole,
    PackageInfo,
    TagInfo,
    TokenRecord,
    TokenRequestRecord,
    TokenRequestStatus,
    TokenRole,
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

    async def list_tokens(self) -> list[TokenRecord]:
        async with get_session() as session:
            result = await session.execute(select(TokenRow).order_by(TokenRow.created_at))
            return [
                TokenRecord(
                    name=row.name,
                    role=TokenRole(row.role),
                    token_hash=row.token_hash,
                    email=row.email,
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

    async def resolve(
        self, request_id: int, status: TokenRequestStatus, reviewed_by: str
    ) -> bool:
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
                    action=AuditAction(last.action),
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
                    action=AuditAction(row.action),
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
                action=AuditAction(r.action),
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
    ) -> tuple[list[PackageInfo], int]:
        async with get_session() as session:
            q = select(PackageRow)
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
                )
                q = q.where(search_filter)
                count_q = count_q.where(search_filter)
            if org_slug:
                q = q.where(PackageRow.org_slug == org_slug)
                count_q = count_q.where(PackageRow.org_slug == org_slug)
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
            packages = [
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
                    org=row.org_slug,
                )
                for row in result.scalars().all()
            ]
            return packages, total

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
                    "org": row.org_slug,
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
    ) -> None:
        async with get_session() as session:
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
            )
            session.add(row)

    async def yank(self, name: str, version: str) -> int:
        async with get_session() as session:
            result = await session.execute(
                update(PackageRow)
                .where(PackageRow.name == name, PackageRow.version == version)
                .values(yanked=True)
            )
            return result.rowcount

    async def unyank(self, name: str, version: str) -> int:
        async with get_session() as session:
            result = await session.execute(
                update(PackageRow)
                .where(PackageRow.name == name, PackageRow.version == version)
                .values(yanked=False)
            )
            return result.rowcount

    async def delete(self, name: str, version: str) -> int:
        from sqlalchemy import delete as sa_delete

        async with get_session() as session:
            result = await session.execute(
                sa_delete(PackageRow).where(PackageRow.name == name, PackageRow.version == version)
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

    async def record(self, package_name: str, version: str, platform: str = "") -> None:
        """Record a download event."""
        async with get_session() as session:
            row = DownloadEventRow(
                package_name=package_name,
                version=version,
                platform=platform,
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

    async def get_daily_downloads(
        self,
        package_name: str = "",
        days: int = 30,
    ) -> list[dict]:
        """Get daily download counts for the last N days.

        Returns list of {"date": "YYYY-MM-DD", "count": N} dicts.
        """
        from sqlalchemy import Date, cast

        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)
        async with get_session() as session:
            q = select(
                cast(DownloadEventRow.downloaded_at, Date).label("day"),
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
            all_tags_rows = (
                await session.execute(
                    select(PackageRow.tags, PackageRow.org_slug)
                    .where(PackageRow.tags != "")
                    .where(PackageRow.yanked == False)  # noqa: E712
                )
            ).all()
            for tags_str, org in all_tags_rows:
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
                    if key not in {
                        f"{r.org_slug}/{r.name}" if r.org_slug else r.name for r in curated
                    }:
                        result[key]["package_count"] = result[key].get("package_count", 0) + 1

            return sorted(result.values(), key=lambda t: t["name"])

    @staticmethod
    async def _count_packages(session, tag_name: str, org_slug: str) -> int:
        """Count non-yanked packages whose comma-separated tags contain *tag_name*."""
        like_pat = f"%{tag_name}%"
        q = select(sa_func.count(PackageRow.id)).where(
            PackageRow.tags.ilike(like_pat),
            PackageRow.yanked == False,  # noqa: E712
        )
        if org_slug:
            q = q.where(PackageRow.org_slug == org_slug)
        return (await session.execute(q)).scalar() or 0
