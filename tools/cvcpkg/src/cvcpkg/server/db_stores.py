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

from sqlalchemy import select, func as sa_func, update

from cvcpkg.server.db import (
    AuditRow,
    PackageRow,
    TokenRow,
    get_session,
)
from cvcpkg.server.models import (
    AuditAction,
    AuditEntry,
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
                select(TokenRow).where(TokenRow.name == name, TokenRow.revoked == False)  # noqa: E712
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
            result = await session.execute(
                select(AuditRow).order_by(AuditRow.id.desc()).limit(1)
            )
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

            total_result = await session.execute(count_q)
            total = total_result.scalar() or 0

            q = q.order_by(PackageRow.name, PackageRow.published_at.desc()).offset(offset).limit(limit)
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
                )
                for row in result.scalars().all()
            ]
            return packages, total

    async def get_catalog_dict(self) -> dict:
        """Return the full catalog as a dict (for /v1/catalog YAML response)."""
        async with get_session() as session:
            result = await session.execute(
                select(PackageRow).where(PackageRow.yanked == False).order_by(PackageRow.name)  # noqa: E712
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
                sa_delete(PackageRow).where(
                    PackageRow.name == name, PackageRow.version == version
                )
            )
            return result.rowcount
