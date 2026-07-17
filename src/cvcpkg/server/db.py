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

import asyncio
import contextvars
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
    text,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.pool import NullPool, StaticPool


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
    # When the bundle was yanked; NULL when it never was, or once unyanked.
    # The yank-retention GC keys on this and treats NULL as "never purge", so
    # rows yanked before the column existed are exempt rather than instantly
    # expired.  See migration 017.
    yanked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
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

    required_deps: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
        server_default="[]",
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
            "org_slug",
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
    # Uniqueness is enforced by uq_tokens_active_name (see __table_args__), not
    # a column-level UNIQUE: a revoked name must be reusable (parity with the
    # YAML TokenStore), which a full column UNIQUE forbids.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
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
    # Rotation grace window: pre-rotation secret hash, honored until
    # previous_hash_expires_at (see DbTokenStore.rotate). Indexed: verify()
    # falls back to this lookup for every failed current-hash match.
    previous_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    previous_hash_expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        # "One *active* token per name" — a revoked name can be reissued, matching
        # the YAML backend.  Postgres and SQLite support partial indexes, so the
        # unique applies only to non-revoked rows.  MySQL ignores the dialect
        # ``*_where`` kwargs and falls back to a full UNIQUE(name); there,
        # DbTokenStore.create() converts the IntegrityError into a clean 409
        # rather than silently reusing the name.  Kept in sync with migration 017.
        Index(
            "uq_tokens_active_name",
            "name",
            unique=True,
            sqlite_where=text("revoked = 0"),
            postgresql_where=text("revoked = false"),
        ),
    )


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
    reviewed_by: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="",
        server_default="",
    )
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
    """Records individual package download events for analytics.

    Privacy: ``client_ip_hash`` is a salted SHA-256 of the client address
    (salted with the server's HMAC key) — the plain IP is never stored.
    All analytics queries aggregate; there is no per-user tracking.
    """

    __tablename__ = "download_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    package_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(255), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    arch: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    client_ip_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    user_agent: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    cvcpkg_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    bytes_sent: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    downloaded_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )

    __table_args__ = (Index("ix_download_events_name_date", "package_name", "downloaded_at"),)


class TelemetryEventRow(Base):
    """Opt-in client telemetry pings (Phase 2 roadmap).

    Strictly anonymous environment fingerprints: platform, arch, Python and
    cvcpkg versions, tool availability, and a CI flag.  No address (not even
    hashed), no hostname, no user, no paths.  Clients only send these when
    ``CVCPKG_TELEMETRY=1`` or via an explicit ``cvcpkg telemetry send``.
    """

    __tablename__ = "telemetry_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    arch: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    python_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    cvcpkg_version: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    ci: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tools: Mapped[str] = mapped_column(Text, nullable=False, default="{}")  # JSON name→version
    received_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        index=True,
    )


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


class BuilderRow(Base):
    """Registered remote build agents."""

    __tablename__ = "builders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    org_slug: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    arch: Mapped[str] = mapped_column(String(64), nullable=False)
    labels: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    capabilities: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="offline")
    current_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_jobs: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    prefer_affinity: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_heartbeat: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registered_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("name", "org_slug", name="uq_builder_name_org"),
        Index("ix_builders_org_slug", "org_slug"),
        Index("ix_builders_platform_arch", "platform", "arch"),
        Index("ix_builders_status", "status"),
    )


class BuildJobRow(Base):
    """Build job queue entries."""

    __tablename__ = "build_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dag_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    org_slug: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    recipe_name: Mapped[str] = mapped_column(String(255), nullable=False)
    recipe_version: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    recipe_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    platform: Mapped[str] = mapped_column(String(64), nullable=False)
    arch: Mapped[str] = mapped_column(String(64), nullable=False)
    config: Mapped[str] = mapped_column(String(32), nullable=False, default="release")
    link: Mapped[str] = mapped_column(String(32), nullable=False, default="shared")
    builder_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("builders.id", ondelete="SET NULL"), nullable=True
    )
    # Identity of an *unregistered* worker that claimed this job (e.g.
    # "gha-run-29372085620").  Platforms with no persistent builder drain their
    # queue anonymously, leaving builder_id NULL; this keeps such a job
    # attributable to the run that is building it.
    claimed_by: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    timeout_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submitted_by: Mapped[str] = mapped_column(String(255), nullable=False)
    submitted_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    log_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    log_size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_archive_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_build_jobs_dag_id", "dag_id"),
        Index("ix_build_jobs_org_slug", "org_slug"),
        Index("ix_build_jobs_status", "status"),
        Index("ix_build_jobs_platform_arch", "platform", "arch"),
        Index("ix_build_jobs_builder_id", "builder_id"),
        Index("ix_build_jobs_recipe_name", "recipe_name"),
    )


class RecipeRow(Base):
    """Server-managed recipe bundles for remote builders."""

    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    recipe_hash: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    org_slug: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    bundle_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    bundle_size: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    uploaded_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    __table_args__ = (
        UniqueConstraint("name", "org_slug", name="uq_recipe_name_org"),
        Index("ix_recipes_name", "name"),
        Index("ix_recipes_org_slug", "org_slug"),
    )


class WebhookRow(Base):
    """Webhook endpoint for event notifications."""

    __tablename__ = "webhooks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    events: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    org_slug: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    secret: Mapped[str] = mapped_column(String(255), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    registered_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    last_delivery_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    consecutive_failures: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    __table_args__ = (
        Index("ix_webhooks_org_slug", "org_slug"),
        Index("ix_webhooks_active", "active"),
    )


class BuildJobDepRow(Base):
    """DAG edges between build jobs."""

    __tablename__ = "build_job_deps"

    job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("build_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    depends_on_job_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("build_jobs.id", ondelete="CASCADE"),
        primary_key=True,
    )

    __table_args__ = (UniqueConstraint("job_id", "depends_on_job_id", name="uq_build_job_dep"),)


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
        # Covers the bare "sqlite+aiosqlite://", "…:///", and explicit
        # "…:///:memory:" forms.
        _path_part = database_url.split("://", 1)[-1].strip("/")
        is_memory = _path_part in ("", ":memory:")
        pool_kwargs: dict = {}
        if is_memory:
            pool_kwargs["poolclass"] = StaticPool
        else:
            # File-backed SQLite: use NullPool so each connection is closed
            # on release rather than kept by AsyncAdaptedQueuePool.  The
            # pooled variant intermittently fails at shutdown with
            # "sqlite3.OperationalError: no active connection": when a task
            # is cancelled mid-session the pool later tries to terminate an
            # aiosqlite connection whose worker thread is already gone.
            # NullPool has no such background-connection lifecycle to race.
            pool_kwargs["poolclass"] = NullPool
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


def database_backend() -> str:
    """Return the active database backend name (e.g. 'sqlite', 'postgresql')."""
    if _engine is None:
        return "unknown"
    try:
        return _engine.url.get_backend_name()
    except Exception:  # noqa: BLE001
        return "unknown"


async def backup_database(dest_dir, timestamp: str):
    """Write a backup of the current database into *dest_dir*.

    Returns ``(path, size_bytes)``.  The backup strategy depends on the
    active backend:

    - **sqlite** — an online ``VACUUM INTO`` snapshot (``.sqlite``).
    - **postgresql** — a ``pg_dump`` plain-SQL dump (``.sql``); requires the
      ``pg_dump`` binary on the server's PATH.
    - **mysql** — a ``mysqldump`` dump (``.sql``); requires ``mysqldump``.

    Raises ``RuntimeError`` if the backend is unsupported or the required
    external tool is missing/fails.
    """
    import shutil
    from pathlib import Path

    if _engine is None:
        raise RuntimeError("database engine is not initialised")

    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    url = _engine.url
    backend = url.get_backend_name()

    if backend == "sqlite":
        db_path = url.database
        if not db_path or db_path == ":memory:":
            raise RuntimeError("cannot back up an in-memory sqlite database")
        dest = dest_dir / f"backup-{timestamp}.sqlite"
        # VACUUM INTO cannot run inside a transaction; use AUTOCOMMIT.
        safe = str(dest).replace("'", "''")
        async with _engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.exec_driver_sql(f"VACUUM INTO '{safe}'")
        return dest, dest.stat().st_size

    if backend == "postgresql":
        if not shutil.which("pg_dump"):
            raise RuntimeError("pg_dump not found on the server PATH")
        dest = dest_dir / f"backup-{timestamp}.sql"
        args = ["pg_dump", "--no-owner", "--no-privileges", "-f", str(dest)]
        if url.host:
            args += ["-h", url.host]
        if url.port:
            args += ["-p", str(url.port)]
        if url.username:
            args += ["-U", url.username]
        if url.database:
            args += ["-d", url.database]
        env = dict(**_os_environ())
        if url.password:
            env["PGPASSWORD"] = url.password
        await _run_dump(args, env, dest)
        return dest, dest.stat().st_size

    if backend == "mysql":
        if not shutil.which("mysqldump"):
            raise RuntimeError("mysqldump not found on the server PATH")
        dest = dest_dir / f"backup-{timestamp}.sql"
        args = ["mysqldump", "--result-file", str(dest)]
        if url.host:
            args += ["-h", url.host]
        if url.port:
            args += ["-P", str(url.port)]
        if url.username:
            args += ["-u", url.username]
        if url.password:
            args += [f"-p{url.password}"]
        if url.database:
            args += [url.database]
        await _run_dump(args, dict(**_os_environ()), dest)
        return dest, dest.stat().st_size

    raise RuntimeError(f"backups are not supported for backend '{backend}'")


def _os_environ() -> dict:
    import os

    return dict(os.environ)


async def _run_dump(args: list, env: dict, dest) -> None:
    """Run a dump subprocess, raising RuntimeError on failure."""
    import asyncio
    from pathlib import Path

    proc = await asyncio.create_subprocess_exec(
        *args,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _out, err = await proc.communicate()
    if proc.returncode != 0:
        Path(dest).unlink(missing_ok=True)
        detail = (err or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(f"dump command failed ({proc.returncode}): {detail}")


# Request-scoped "ambient" session for the unit-of-work pattern.  When set
# (by atomic_session), every get_session() in the same async context joins
# that transaction instead of opening its own — so a store mutation and its
# audit entry commit atomically.  Default None ⇒ unchanged standalone
# behaviour for every existing caller.
_current_session: contextvars.ContextVar[AsyncSession | None] = contextvars.ContextVar(
    "cvcpkg_current_session", default=None
)


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session for database operations.

    If an ambient unit-of-work session is active (see ``atomic_session``),
    yield it *without* committing — the owner of the unit of work commits
    once at the end.  Otherwise open a fresh session that commits on
    success and rolls back on error (the standalone behaviour).
    """
    ambient = _current_session.get()
    if ambient is not None:
        yield ambient
        return
    if _session_factory is None:
        raise RuntimeError("call init_db() first")
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def atomic_session() -> AsyncGenerator[AsyncSession, None]:
    """Run a block as one transaction that every ``get_session()`` joins.

    Store mutations and the audit write inside the block share a single
    session and commit together (all-or-nothing): a crash between them can
    no longer leave a mutation applied but unlogged.  On exception the
    whole unit of work rolls back.
    """
    if _session_factory is None:
        raise RuntimeError("call init_db() first")
    async with _session_factory() as session:
        token = _current_session.set(session)
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            _current_session.reset(token)


def in_atomic_session() -> bool:
    """True when an ambient unit-of-work session is active."""
    return _current_session.get() is not None


# ── Audit-append serialization ─────────────────────────────────
#
# The audit chain hashes each entry against the previous one, so the
# read-last-row → insert step must be serialized: two concurrent appends
# that both read the same "last" row fork the chain and make
# verify_chain() report false tampering.  A process-local lock serializes
# appends within a worker; a per-event-loop instance avoids the
# "bound to a different event loop" error when tests spin up fresh loops
# via asyncio.run().  Multi-worker Postgres additionally takes a
# transaction-scoped advisory lock (see audit_advisory_lock).

_AUDIT_ADVISORY_KEY = 0x63766361  # "cvca" — fixed key for pg_advisory
_append_lock: asyncio.Lock | None = None
_append_lock_loop: asyncio.AbstractEventLoop | None = None


def audit_append_lock() -> asyncio.Lock:
    """Return the audit-append lock bound to the running event loop."""
    global _append_lock, _append_lock_loop
    loop = asyncio.get_running_loop()
    if _append_lock is None or _append_lock_loop is not loop:
        _append_lock = asyncio.Lock()
        _append_lock_loop = loop
    return _append_lock


async def audit_advisory_lock(session: AsyncSession) -> None:
    """Take a transaction-scoped advisory lock on Postgres.

    Serializes audit-chain appends across worker processes (the
    process-local ``audit_append_lock`` only covers one worker).  Released
    automatically when the transaction commits or rolls back.  A no-op on
    SQLite/other backends, which are single-process in practice.
    """
    if _engine is None:
        return
    if _engine.url.get_backend_name() == "postgresql":
        from sqlalchemy import text

        await session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _AUDIT_ADVISORY_KEY})
