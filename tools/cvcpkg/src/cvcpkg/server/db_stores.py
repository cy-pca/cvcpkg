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
    OrganizationRow,
    OrgMemberRow,
    PackageRow,
    TokenRow,
    get_session,
)
from cvcpkg.server.models import (
    AuditAction,
    AuditEntry,
    OrgInfo,
    OrgMember,
    OrgRole,
    PackageInfo,
    TokenRecord,
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

    async def list_tokens(self) -> list[TokenRecord]:
        async with get_session() as session:
            result = await session.execute(select(TokenRow).order_by(TokenRow.created_at))
            return [
                TokenRecord(
                    name=row.name,
                    role=TokenRole(row.role),
                    token_hash=row.token_hash,
                    created_at=row.created_at,
                    expires_at=row.expires_at,
                    revoked=row.revoked,
                )
                for row in result.scalars().all()
            ]


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
        limit: int = 1000,
        offset: int = 0,
    ) -> tuple[list[PackageInfo], int]:
        async with get_session() as session:
            q = select(PackageRow)
            count_q = select(sa_func.count(PackageRow.id))
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
                )
                for row in result.scalars().all()
            ]
            return packages, total

    async def get_catalog_dict(self) -> dict:
        """Return the full catalog as a dict (for /v1/catalog YAML response)."""
        async with get_session() as session:
            result = await session.execute(
                select(PackageRow)
                .where(PackageRow.yanked == False)  # noqa: E712
                .order_by(PackageRow.name)
            )
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

    async def list_orgs(self, *, limit: int = 100, offset: int = 0) -> tuple[list[OrgInfo], int]:
        async with get_session() as session:
            count_q = select(sa_func.count(OrganizationRow.id))
            total = (await session.execute(count_q)).scalar() or 0

            q = (
                select(OrganizationRow)
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
            storage_limit_bytes=row.storage_limit_bytes,
            storage_used_bytes=row.storage_used_bytes,
            created_at=row.created_at,
            created_by=row.created_by,
        )
