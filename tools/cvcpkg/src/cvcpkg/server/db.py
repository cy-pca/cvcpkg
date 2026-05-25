"""PostgreSQL database backend for cvcpkg-server.

Provides SQLAlchemy models and async session management.  The server
uses PostgreSQL for all persistent state (packages, tokens, audit log)
when ``CVCPKG_DATABASE_URL`` is set; otherwise falls back to the
original YAML-file backend.

Tables:
    packages   — published bundle metadata
    tokens     — API token records (HMAC-SHA256 hashes)
    audit_log  — tamper-evident audit entries with chained hashes
"""

from __future__ import annotations

import datetime
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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

    __table_args__ = (
        Index(
            "ix_packages_unique_variant",
            "name", "version", "platform", "arch", "build_type", "link",
            unique=True,
        ),
    )


class TokenRow(Base):
    __tablename__ = "tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


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


# ── Engine / session management ─────────────────────────────────

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db(database_url: str) -> None:
    """Initialise the async engine and session factory.

    Call this once at startup with a URL like
    ``postgresql+asyncpg://user:pass@host/dbname``.
    """
    global _engine, _session_factory
    _engine = create_async_engine(database_url, echo=False, pool_size=10, max_overflow=20)
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
