"""Database backend for cvcpkg-server.

Provides SQLAlchemy models and async session management.  The server
uses a SQL database for all persistent state (packages, tokens, audit
log) when ``CVCPKG_DATABASE_URL`` is set; otherwise falls back to the
original YAML-file backend.

Supported backends:
    - PostgreSQL: ``postgresql+asyncpg://user:pass@host/dbname``
    - SQLite:     ``sqlite+aiosqlite:///path/to/db.sqlite``
                  ``sqlite+aiosqlite://`` (in-memory)
    - MySQL:      ``mysql+aiomysql://user:pass@host/dbname``

Tables:
    packages   — published bundle metadata
    tokens     — API token records (HMAC-SHA256 hashes)
    audit_log  — tamper-evident audit entries with chained hashes
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


# ── ORM Models ──────────────────────────────────────────────────


class PackageRow(Base):
    __tablename__ = "packages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    arch: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    build_type: Mapped[str] = mapped_column(String(32), nullable=False, default="release")
    link: Mapped[str] = mapped_column(String(32), nullable=False, default="shared")
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    archive_url: Mapped[str] = mapped_column(Text, nullable=False)
    published_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    yanked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    signature: Mapped[str] = mapped_column(Text, nullable=False, default="")
    key_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    release_tag: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
        server_default="",
        index=True,
    )
    recipe_version: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="",
        server_default="",
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    homepage: Mapped[str] = mapped_column(
        String(512), nullable=False, default="", server_default=""
    )
    license: Mapped[str] = mapped_column(String(128), nullable=False, default="", server_default="")
    maintainer: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", server_default=""
    )
    tags: Mapped[str] = mapped_column(Text, nullable=False, default="")
    published_by: Mapped[str] = mapped_column(
        String(255), nullable=False, default="", server_default=""
    )

    org_slug: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
        server_default="",
        index=True,
    )

    __table_args__ = (
        Index(
            "ix_packages_unique_variant",
            "name",
            "version",
            "platform",
            "arch",
            "build_type",
            "link",
            unique=True,
        ),
    )


class OrganizationRow(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    logo_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    homepage: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    is_private: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    storage_limit_bytes: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=10 * 1024 * 1024 * 1024,
    )
    storage_used_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)


class OrgMemberRow(Base):
    __tablename__ = "org_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    token_name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    added_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("org_id", "token_name", name="uq_org_member"),
        Index("ix_org_members_org_id", "org_id"),
        Index("ix_org_members_token_name", "token_name"),
    )


class TokenRow(Base):
    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    user_metadata: Mapped[str] = mapped_column(Text, nullable=False, default="", server_default="")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class TokenRequestRow(Base):
    """Pending token registration requests (admin-gated mode)."""

    __tablename__ = "token_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="reader")
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    reviewed_by: Mapped[str] = mapped_column(String(255), nullable=False, default="", server_default="")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    resolved_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuditRow(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    target: Mapped[str] = mapped_column(String(512), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    prev_sha256: Mapped[str] = mapped_column(String(64), nullable=False, default="")


class DownloadEventRow(Base):
    """Records individual package download events for analytics."""

    __tablename__ = "download_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    package_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    downloaded_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    __table_args__ = (Index("ix_download_events_name_date", "package_name", "downloaded_at"),)


class TagRow(Base):
    """Curated tag metadata for the browse-by-tag front page.

    Tags are org-scoped: ``org_slug`` is empty for global tags and set
    to an organization slug for org-level tags.  The ``(name, org_slug)``
    pair is unique.
    """

    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    org_slug: Mapped[str] = mapped_column(String(64), nullable=False, default="", server_default="")
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    logo_url: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    __table_args__ = (
        UniqueConstraint("name", "org_slug", name="uq_tag_name_org"),
        Index("ix_tags_org_slug", "org_slug"),
    )


class MirrorRow(Base):
    """Registered mirror servers tracked by the primary."""

    __tablename__ = "mirrors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    contact: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    registered_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    last_health_check: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_healthy_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    healthy: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rejected_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rejected_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    packages_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# ── Engine / session management ─────────────────────────────────

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db(database_url: str) -> None:
    """Initialise the async engine and session factory.

    Call this once at startup with a URL like:
        ``postgresql+asyncpg://user:pass@host/dbname``
        ``sqlite+aiosqlite:///path/to/db.sqlite``
        ``mysql+aiomysql://user:pass@host/dbname``
    """
    global _engine, _session_factory

    # SQLite doesn't support connection pooling options
    is_sqlite = database_url.startswith("sqlite")
    if is_sqlite:
        # In-memory SQLite (no path after "://") needs StaticPool so every
        # connection shares the same database instead of creating a new one.
        is_memory = database_url.rstrip("/") in (
            "sqlite+aiosqlite://",
            "sqlite+aiosqlite:///",
            "sqlite+aiosqlite:///:memory:",
        )
        pool_kwargs: dict = {}
        if is_memory:
            pool_kwargs["poolclass"] = StaticPool
        _engine = create_async_engine(
            database_url,
            echo=False,
            connect_args={"check_same_thread": False},
            **pool_kwargs,
        )
    else:
        _engine = create_async_engine(
            database_url,
            echo=False,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600,
        )
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def create_tables() -> None:
    """Create all tables (idempotent — uses CREATE IF NOT EXISTS)."""
    if _engine is None:
        raise RuntimeError("call init_db() first")
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Dispose the engine connection pool."""
    if _engine is not None:
        await _engine.dispose()


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session for database operations."""
    if _session_factory is None:
        raise RuntimeError("call init_db() first")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
