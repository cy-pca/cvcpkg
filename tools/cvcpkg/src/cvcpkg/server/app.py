"""FastAPI application for cvcpkg-server.

This module defines all REST API endpoints for package publishing,
serving, catalog management, token administration, and audit trail
inspection.

All mutating endpoints require a bearer token with an appropriate
role.  Read-only endpoints (GET /v1/catalog, GET /v1/packages,
GET /v1/download) are unauthenticated by default but can be locked
down via configuration.

When ``CVCPKG_DATABASE_URL`` is set the server uses PostgreSQL for
persistent state; otherwise it falls back to YAML files on disk.
"""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import logging
import os
import re as _re
import secrets
import signal
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)

from cvcpkg import __version__
from cvcpkg.server.audit import AuditLog
from cvcpkg.server.auth import TokenStore
from cvcpkg.server.models import (
    AuditAction,
    AuditLogResponse,
    BuilderHeartbeatRequest,
    BuilderInfo,
    BuilderListResponse,
    BuilderRegisterRequest,
    BuilderUpdateRequest,
    BuildJobClaimRequest,
    BuildJobCompleteRequest,
    BuildJobFailRequest,
    BuildJobInfo,
    BuildJobListResponse,
    BuildJobSubmitRequest,
    BuildLogAppendRequest,
    CacheStatusResponse,
    CatalogResponse,
    DagSubmitRequest,
    DagSubmitResponse,
    EmailUpdateRequest,
    HealthResponse,
    MirrorInfo,
    MirrorListResponse,
    MirrorRegisterRequest,
    OrgCreateRequest,
    OrgDetailResponse,
    OrgInfo,
    OrgListResponse,
    OrgRole,
    OrgUpdateRequest,
    PackageInfo,
    PackageListResponse,
    ProfileUpdateRequest,
    PublishResponse,
    RecipeInfo,
    RecipeListResponse,
    RegistrationMode,
    RegistrationRequest,
    RegistrationResponse,
    TagCreateRequest,
    TagInfo,
    TagListResponse,
    TagUpdateRequest,
    TokenCreateRequest,
    TokenCreateResponse,
    TokenRecord,
    TokenRequestListResponse,
    TokenRequestStatus,
    TokenRole,
    UserListResponse,
    UserProfileResponse,
    WebhookInfo,
    WebhookListResponse,
    WebhookRegisterRequest,
    WebhookUpdateRequest,
)

# ── State ───────────────────────────────────────────────────────

_INDEX_FILE = "index.yaml"
_ARCHIVES_DIR = "archives"
_LOGS_DIR = "logs"
_START_TIME = 0.0

# ── Configurable limits ────────────────────────────────────────

# Maximum upload size in bytes (default 512 MiB)
MAX_UPLOAD_BYTES = int(os.environ.get("CVCPKG_MAX_UPLOAD_BYTES", str(1024 * 1024 * 1024)))

# Chunked upload chunk size (default 8 MiB)
CHUNK_SIZE = int(os.environ.get("CVCPKG_CHUNK_SIZE", str(8 * 1024 * 1024)))

# Upload session expiry in seconds (default 1 hour)
UPLOAD_SESSION_TTL = int(os.environ.get("CVCPKG_UPLOAD_SESSION_TTL", "3600"))

# Rate limiting: requests per minute for write endpoints
RATE_LIMIT_RPM = int(os.environ.get("CVCPKG_RATE_LIMIT_RPM", "300"))

# Default storage limit per organization in bytes (default 10 GiB)
ORG_STORAGE_LIMIT_BYTES = int(
    os.environ.get("CVCPKG_ORG_STORAGE_LIMIT_BYTES", str(10 * 1024 * 1024 * 1024))
)

# Global cache storage limit in bytes across all namespaces (default 100 GiB, 0 = unlimited)
GLOBAL_CACHE_STORAGE_LIMIT_BYTES = int(
    os.environ.get("CVCPKG_GLOBAL_CACHE_STORAGE_LIMIT_BYTES", str(100 * 1024 * 1024 * 1024))
)

# CORS allowed origins (comma-separated, empty = deny all cross-origin)
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("CVCPKG_CORS_ORIGINS", "").split(",") if o.strip()
]

# Download stats graph configuration
DOWNLOAD_GRAPH_DAYS = int(os.environ.get("CVCPKG_DOWNLOAD_GRAPH_DAYS", "30"))
DOWNLOAD_GRAPH_COLOR = os.environ.get("CVCPKG_DOWNLOAD_GRAPH_COLOR", "#3273dc")
DOWNLOAD_GRAPH_FILL_COLOR = os.environ.get(
    "CVCPKG_DOWNLOAD_GRAPH_FILL_COLOR", "rgba(50,115,220,0.15)"
)
DOWNLOAD_GRAPH_HEIGHT = int(os.environ.get("CVCPKG_DOWNLOAD_GRAPH_HEIGHT", "200"))

# ── Mirror configuration ────────────────────────────────────────

# When True, the server runs as a read-only mirror of an upstream.
MIRROR_MODE = os.environ.get("CVCPKG_MIRROR_MODE", "").lower() in ("1", "true", "yes")

# Upstream server URL to mirror from (required when MIRROR_MODE is True).
MIRROR_UPSTREAM = os.environ.get("CVCPKG_MIRROR_UPSTREAM", "")

# Token for accessing private packages on the upstream server.
MIRROR_TOKEN = os.environ.get("CVCPKG_MIRROR_TOKEN", "")

# Sync interval in seconds (how often the mirror pulls the upstream catalog).
MIRROR_SYNC_INTERVAL = int(os.environ.get("CVCPKG_MIRROR_SYNC_INTERVAL", "3600"))

# Health check interval for registered mirrors (on the primary).
MIRROR_HEALTH_CHECK_INTERVAL = int(os.environ.get("CVCPKG_MIRROR_HEALTH_CHECK_INTERVAL", "300"))

# Consecutive health check failures before marking a mirror unhealthy.
MIRROR_MAX_FAILURES = int(os.environ.get("CVCPKG_MIRROR_MAX_FAILURES", "3"))

# Registration mode: "open" (default) or "admin-gated".
REGISTRATION_MODE = RegistrationMode(os.environ.get("CVCPKG_REGISTRATION_MODE", "open"))

logger = logging.getLogger("cvcpkg.server")


class ServerState:
    """Mutable server-wide state, initialised at startup."""

    state_dir: Path
    storage_uri: str
    tokens: TokenStore
    audit: AuditLog
    index: dict  # The in-memory catalog index
    require_auth_for_reads: bool

    def __init__(
        self,
        state_dir: Path,
        storage_uri: str = "",
        require_auth_for_reads: bool = False,
    ) -> None:
        self.state_dir = state_dir
        self.storage_uri = storage_uri or f"file://{state_dir}"
        self.require_auth_for_reads = require_auth_for_reads
        self.tokens = TokenStore(state_dir)
        self.audit = AuditLog(state_dir)
        self.index = self._load_index()

    def _load_index(self) -> dict:
        idx_path = self.state_dir / _INDEX_FILE
        if idx_path.is_file():
            with open(idx_path) as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                return data
        return {"schema_version": 1, "revision": 0, "bundles": []}

    def save_index(self) -> None:
        self.index["revision"] = self.index.get("revision", 0) + 1
        idx_path = self.state_dir / _INDEX_FILE
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(idx_path, "w") as f:
            yaml.safe_dump(self.index, f, default_flow_style=False, sort_keys=False)

    def archives_dir(self) -> Path:
        d = self.state_dir / _ARCHIVES_DIR
        d.mkdir(parents=True, exist_ok=True)
        return d

    def logs_dir(self) -> Path:
        d = self.state_dir / _LOGS_DIR
        d.mkdir(parents=True, exist_ok=True)
        return d


# Singleton — set by create_app() or lifespan
_state: ServerState | None = None
_use_db: bool = False
_db_tokens = None  # DbTokenStore when using DB backend
_db_audit = None  # DbAuditLog when using DB backend
_db_packages = None  # DbPackageIndex when using DB backend
_db_orgs = None  # DbOrgStore when using DB backend
_db_downloads = None  # DbDownloadStore when using DB backend
_db_mirrors = None  # DbMirrorStore when using DB backend
_db_tags = None  # DbTagStore when using DB backend
_db_token_requests = None  # DbTokenRequestStore when using DB backend
_db_builders = None  # DbBuilderStore when using DB backend
_db_build_jobs = None  # DbBuildJobStore when using DB backend
_db_recipes = None  # DbRecipeStore when using DB backend
_db_webhooks = None  # DbWebhookStore when using DB backend


def _get_state() -> ServerState:
    if _state is None:
        raise RuntimeError("server not initialised")
    return _state


# ── Chunked upload session tracking ────────────────────────────


@dataclass
class UploadSession:
    """Tracks an in-progress chunked upload."""

    upload_id: str
    name: str
    version: str
    platform: str
    arch: str
    build_type: str
    link: str
    signature: str
    key_fingerprint: str
    release_tag: str
    recipe_version: str
    actor_name: str
    temp_path: Path
    description: str = ""
    homepage: str = ""
    pkg_license: str = ""
    maintainer: str = ""
    tags: str = ""
    required_deps: str = "[]"
    hasher: hashlib._Hash = field(default_factory=lambda: hashlib.sha256())
    bytes_received: int = 0
    total_size: int = 0  # 0 = unknown
    created_at: float = field(default_factory=time.monotonic)


# Map of upload_id -> UploadSession (process-local, single-worker safe)
_upload_sessions: dict[str, UploadSession] = {}


def _purge_expired_sessions() -> None:
    """Remove upload sessions older than UPLOAD_SESSION_TTL."""
    now = time.monotonic()
    expired = [
        uid for uid, s in _upload_sessions.items() if now - s.created_at > UPLOAD_SESSION_TTL
    ]
    for uid in expired:
        s = _upload_sessions.pop(uid, None)
        if s and s.temp_path.exists():
            s.temp_path.unlink(missing_ok=True)


# ── Helpers ─────────────────────────────────────────────────────

_DURATION_RE = _re.compile(r"^(\d+(?:\.\d+)?)\s*([smhd])$", _re.IGNORECASE)


def _parse_duration(value: str) -> float:
    """Parse a duration string like '14d', '2h', '30m' into seconds."""
    m = _DURATION_RE.match(value.strip())
    if not m:
        raise HTTPException(422, f"Invalid duration: {value!r}. Use e.g. '14d', '2h', '30m'.")
    amount = float(m.group(1))
    unit = m.group(2).lower()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return amount * multipliers[unit]


# ── Auth dependency ─────────────────────────────────────────────


def _extract_token(authorization: str | None = Header(None)) -> str | None:
    """Pull the bearer token from the Authorization header."""
    if authorization is None:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def require_role(*roles: TokenRole):
    """FastAPI dependency that requires one of the given roles."""

    async def _dep(authorization: str | None = Header(None)) -> TokenRecord:
        state = _get_state()
        raw = _extract_token(authorization)
        if raw is None:
            raise HTTPException(401, "missing Authorization header")
        if _use_db:
            record = await _db_tokens.verify(raw)
        else:
            record = state.tokens.verify(raw)
        if record is None:
            raise HTTPException(401, "invalid or expired token")
        if record.role not in roles:
            raise HTTPException(
                403,
                f"role '{record.role.value}' not in {[r.value for r in roles]}",
            )
        return record

    return _dep


async def optional_reader_auth(authorization: str | None = Header(None)) -> TokenRecord | None:
    """For read endpoints: enforce auth only if configured."""
    state = _get_state()
    if not state.require_auth_for_reads:
        return None
    raw = _extract_token(authorization)
    if raw is None:
        raise HTTPException(401, "this server requires authentication for reads")
    if _use_db:
        record = await _db_tokens.verify(raw)
    else:
        record = state.tokens.verify(raw)
    if record is None:
        raise HTTPException(401, "invalid or expired token")
    return record


async def optional_token(authorization: str | None = Header(None)) -> TokenRecord | None:
    """Try to resolve a bearer token if present, but never require it."""
    raw = _extract_token(authorization)
    if raw is None:
        return None
    if _use_db:
        return await _db_tokens.verify(raw)
    state = _get_state()
    return state.tokens.verify(raw)


async def _authenticate_token(raw: str) -> TokenRecord | None:
    """Verify a raw token string and return the record (or None)."""
    if _use_db:
        return await _db_tokens.verify(raw)
    state = _get_state()
    return state.tokens.verify(raw)


# ── Webhook delivery engine ────────────────────────────────────

_WEBHOOK_RETRY_DELAYS = [10, 60, 300]  # seconds between retries


async def _deliver_webhook(
    webhook_id: int,
    url: str,
    secret: str,
    event: str,
    payload: str,
) -> bool:
    """Attempt to deliver a webhook payload.  Returns True on success."""
    import hashlib as _h
    import hmac as _hm
    import uuid

    import httpx

    sig = _hm.new(secret.encode(), payload.encode(), _h.sha256).hexdigest()
    delivery_id = str(uuid.uuid4())
    headers = {
        "Content-Type": "application/json",
        "X-CvcPkg-Event": event,
        "X-CvcPkg-Signature": f"sha256={sig}",
        "X-CvcPkg-Delivery": delivery_id,
    }
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, content=payload, headers=headers)
        if resp.status_code < 400:
            await _db_webhooks.record_delivery(webhook_id)
            return True
        logger.warning(
            "webhook %d delivery to %s returned %d",
            webhook_id,
            url,
            resp.status_code,
        )
    except Exception as exc:
        logger.warning("webhook %d delivery to %s failed: %s", webhook_id, url, exc)
    await _db_webhooks.record_failure(webhook_id)
    return False


async def _deliver_with_retries(
    webhook_id: int,
    url: str,
    secret: str,
    event: str,
    payload: str,
) -> None:
    """Deliver a webhook with retries (exponential backoff)."""
    if await _deliver_webhook(webhook_id, url, secret, event, payload):
        return
    for delay in _WEBHOOK_RETRY_DELAYS:
        await asyncio.sleep(delay)
        # Re-check that the webhook is still active
        info = await _db_webhooks.get(webhook_id)
        if info is None or not info.active:
            return
        if await _deliver_webhook(webhook_id, url, secret, event, payload):
            return


async def emit_webhook_event(
    event: str,
    data: dict,
    *,
    org_slug: str = "",
) -> None:
    """Fire a webhook event to all matching active webhooks.

    Deliveries run as background tasks so the caller is not blocked.
    """
    if not _use_db or _db_webhooks is None:
        return
    try:
        hooks = await _db_webhooks.list_active_for_event(event, org_slug=org_slug)
    except Exception:
        logger.exception("failed to list webhooks for event %s", event)
        return
    if not hooks:
        return

    payload = json.dumps(
        {
            "event": event,
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "data": data,
        }
    )

    for hook in hooks:
        secret = await _db_webhooks.get_secret(hook.id)
        asyncio.create_task(
            _deliver_with_retries(
                hook.id,
                hook.url,
                secret or "",
                event,
                payload,
            )
        )


# ── Connected WebSocket builders ───────────────────────────────

# Maps builder_id -> WebSocket for all connected builders.
_ws_builders: dict[int, WebSocket] = {}


async def _ws_send(builder_id: int, msg: dict) -> bool:
    """Send a JSON message to a connected builder.  Returns True on success."""
    ws = _ws_builders.get(builder_id)
    if ws is None:
        return False
    try:
        await ws.send_json(msg)
        return True
    except Exception:
        _ws_builders.pop(builder_id, None)
        return False


async def _ws_broadcast(msg: dict) -> None:
    """Send a JSON message to all connected builders."""
    dead: list[int] = []
    for bid, ws in _ws_builders.items():
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(bid)
    for bid in dead:
        _ws_builders.pop(bid, None)


# ── Build scheduler background task ────────────────────────────

_SCHEDULER_INTERVAL = int(os.environ.get("CVCPKG_SCHEDULER_INTERVAL", "5"))
_DEFAULT_BUILD_TIMEOUT = int(os.environ.get("CVCPKG_BUILD_TIMEOUT", "86400"))

# Log retention: 0 means disabled (no automatic GC)
_LOG_RETENTION_DAYS = int(os.environ.get("CVCPKG_LOG_RETENTION_DAYS", "0"))
# How often the log retention GC runs (seconds, default 1 hour)
_LOG_GC_INTERVAL = int(os.environ.get("CVCPKG_LOG_GC_INTERVAL", "3600"))


async def _build_scheduler_loop() -> None:
    """Continuously match ready jobs to available builders."""
    import asyncio

    while True:
        await asyncio.sleep(_SCHEDULER_INTERVAL)
        if not _use_db or _db_build_jobs is None or _db_builders is None:
            continue
        try:
            # 1. Reap stale builders (missed heartbeats)
            await _db_builders.reap_stale(max_age_seconds=180)

            # 2. Reap timed-out jobs
            timed_out = await _db_build_jobs.reap_timed_out(_DEFAULT_BUILD_TIMEOUT)
            for job in timed_out:
                await _db_build_jobs.cancel_downstream(job.id)
                # Notify builder via WebSocket
                if job.builder_id is not None:
                    await _ws_send(
                        job.builder_id,
                        {
                            "type": "job.timeout",
                            "job_id": job.id,
                            "message": f"exceeded {_DEFAULT_BUILD_TIMEOUT}s limit",
                        },
                    )
                await emit_webhook_event(
                    "build.timed_out",
                    {
                        "job_id": job.id,
                        "recipe_name": job.recipe_name,
                        "platform": job.platform,
                        "arch": job.arch,
                    },
                    org_slug=job.org_slug,
                )

            # 3. Find ready jobs and match to builders
            ready_jobs = await _db_build_jobs.find_ready_jobs()
            if not ready_jobs:
                continue

            online_builders = await _db_builders.list_builders(status="online")
            # Include "busy" builders that still have capacity
            busy_builders = await _db_builders.list_builders(status="busy")
            available = []
            for b in online_builders + busy_builders:
                if b.current_jobs < b.max_jobs:
                    available.append(b)

            for job in ready_jobs:
                # Find matching builder (platform + arch)
                candidates = [
                    b
                    for b in available
                    if b.platform == job.platform
                    and b.arch == job.arch
                    and b.current_jobs < b.max_jobs
                ]
                if not candidates:
                    continue

                # Prefer affinity builders (soft preference)
                affinity = [b for b in candidates if b.prefer_affinity]
                chosen = affinity[0] if affinity else candidates[0]

                await _db_build_jobs.dispatch(job.id, chosen.id)
                # Notify builder via WebSocket if connected
                dispatched = await _db_build_jobs.get(job.id)
                if dispatched is not None:
                    await _ws_send(
                        chosen.id,
                        {
                            "type": "job.dispatch",
                            "job": json.loads(dispatched.model_dump_json()),
                        },
                    )
                # Update builder's current_jobs count
                await _db_builders.heartbeat(
                    chosen.id,
                    status="busy" if chosen.current_jobs + 1 >= chosen.max_jobs else "online",
                    current_jobs=chosen.current_jobs + 1,
                )
                chosen.current_jobs += 1  # update in-memory for this loop iteration

        except Exception:
            logger.exception("build scheduler loop error")


async def _log_retention_gc_loop() -> None:
    """Periodically delete logs older than the retention policy."""
    import asyncio

    while True:
        await asyncio.sleep(_LOG_GC_INTERVAL)
        if _LOG_RETENTION_DAYS <= 0:
            continue
        if not _use_db or _db_build_jobs is None or _state is None:
            continue
        try:
            purged = await _db_build_jobs.purge_old_logs(
                older_than_days=_LOG_RETENTION_DAYS,
                logs_dir=_state.logs_dir(),
            )
            if purged:
                logger.info("log retention GC: purged %d old logs", purged)
        except Exception:
            logger.exception("log retention GC error")


# ── Mirror background tasks ─────────────────────────────────────


async def _mirror_health_loop() -> None:
    """Periodically health-check registered mirrors on the primary."""
    import asyncio

    import httpx

    while True:
        await asyncio.sleep(MIRROR_HEALTH_CHECK_INTERVAL)
        if not _use_db or _db_mirrors is None:
            continue
        try:
            mirrors = await _db_mirrors.list_all()
            for m in mirrors:
                if m.rejected:
                    continue
                try:
                    async with httpx.AsyncClient(timeout=10) as client:
                        resp = await client.get(f"{m.url.rstrip('/')}/healthz")
                    healthy = resp.status_code == 200
                    if healthy:
                        try:
                            data = resp.json()
                            count = data.get("packages_count", 0)
                            await _db_mirrors.update_packages_count(m.url, count)
                        except Exception:
                            pass
                except Exception:
                    healthy = False
                await _db_mirrors.record_health_check(m.url, healthy=healthy)
        except Exception:
            logger.exception("mirror health check loop error")


async def _mirror_sync_loop(state_dir: Path) -> None:
    """Periodically sync the catalog from the upstream server (mirror mode)."""
    import asyncio

    import httpx

    while True:
        try:
            headers: dict[str, str] = {}
            if MIRROR_TOKEN:
                headers["Authorization"] = f"Bearer {MIRROR_TOKEN}"
            upstream = MIRROR_UPSTREAM.rstrip("/")
            async with httpx.AsyncClient(timeout=60, headers=headers) as client:
                resp = await client.get(f"{upstream}/v1/catalog")
                resp.raise_for_status()
                catalog = resp.json()

            state = _get_state()
            state.index = catalog
            state.save_index()
            bundle_count = len(catalog.get("bundles", []))
            logger.info(
                "mirror sync: updated catalog from %s (%d bundles)",
                upstream,
                bundle_count,
            )
        except Exception:
            logger.exception("mirror sync failed")

        await asyncio.sleep(MIRROR_SYNC_INTERVAL)


# ── Username validation ─────────────────────────────────────────

_C_IDENTIFIER_RE = _re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")


def _is_valid_identifier(name: str) -> bool:
    """Check that *name* conforms to C-identifier-like rules.

    Must start with a letter or underscore, followed by letters, digits,
    underscores, or hyphens.
    """
    return bool(_C_IDENTIFIER_RE.match(name))


# ── App factory ─────────────────────────────────────────────────


def create_app(
    state_dir: Path | None = None,
    storage_uri: str = "",
    require_auth_for_reads: bool = False,
) -> FastAPI:
    """Build and return the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _state, _START_TIME, _use_db
        global _db_tokens, _db_audit, _db_packages, _db_orgs
        global _db_downloads, _db_mirrors, _db_tags, _db_token_requests
        global _db_builders
        global _db_build_jobs
        global _db_recipes
        global _db_webhooks
        _START_TIME = time.monotonic()
        sd = state_dir or Path(os.environ.get("CVCPKG_SERVER_STATE_DIR", "/var/lib/cvcpkg-server"))

        # Register graceful shutdown on SIGTERM (only works in main thread)
        import threading

        if threading.current_thread() is threading.main_thread():

            def _handle_sigterm(signum, frame):
                logger.info("received SIGTERM — shutting down gracefully")
                raise SystemExit(0)

            signal.signal(signal.SIGTERM, _handle_sigterm)

        db_url = os.environ.get("CVCPKG_DATABASE_URL", "")
        if db_url:
            from cvcpkg.server.db import create_tables, dispose_engine, init_db
            from cvcpkg.server.db_stores import (
                DbAuditLog,
                DbBuilderStore,
                DbBuildJobStore,
                DbDownloadStore,
                DbMirrorStore,
                DbOrgStore,
                DbPackageIndex,
                DbRecipeStore,
                DbTagStore,
                DbTokenRequestStore,
                DbTokenStore,
                DbWebhookStore,
            )

            init_db(db_url)
            await create_tables()
            _db_tokens = DbTokenStore(sd)
            _db_audit = DbAuditLog()
            _db_packages = DbPackageIndex()
            _db_orgs = DbOrgStore()
            _db_downloads = DbDownloadStore()
            _db_mirrors = DbMirrorStore()
            _db_tags = DbTagStore()
            _db_token_requests = DbTokenRequestStore()
            _db_builders = DbBuilderStore()
            _db_build_jobs = DbBuildJobStore()
            _db_recipes = DbRecipeStore()
            _db_webhooks = DbWebhookStore()
            _use_db = True
            # Still need ServerState for archives dir and storage_uri
            _state = ServerState(
                sd,
                storage_uri=storage_uri,
                require_auth_for_reads=require_auth_for_reads,
            )

            # Start background tasks
            import asyncio

            bg_tasks: list[asyncio.Task] = []
            if _use_db and not MIRROR_MODE:
                bg_tasks.append(asyncio.create_task(_mirror_health_loop()))
                bg_tasks.append(asyncio.create_task(_build_scheduler_loop()))
                if _LOG_RETENTION_DAYS > 0:
                    bg_tasks.append(asyncio.create_task(_log_retention_gc_loop()))
            if MIRROR_MODE and MIRROR_UPSTREAM:
                bg_tasks.append(asyncio.create_task(_mirror_sync_loop(sd)))

            yield

            # Cancel background tasks
            for t in bg_tasks:
                t.cancel()
            for t in bg_tasks:
                try:
                    await t
                except asyncio.CancelledError:
                    pass

            logger.info("shutting down — disposing database engine")
            await dispose_engine()
            _use_db = False
            _state = None
        else:
            _use_db = False
            _state = ServerState(
                sd,
                storage_uri=storage_uri,
                require_auth_for_reads=require_auth_for_reads,
            )
            yield
            logger.info("shutting down")
            _state = None

    app = FastAPI(
        title="cvcpkg-server",
        version=__version__,
        description="Package server for libcvc-deps component bundles",
        lifespan=lifespan,
    )

    # ── CORS middleware ───────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOWED_ORIGINS,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    # ── Rate limiting state (per-IP, sliding window) ──────
    _rate_buckets: dict[str, list[float]] = {}

    def _check_rate_limit(request: Request) -> None:
        """Raise 429 if the client exceeds RATE_LIMIT_RPM requests/minute."""
        if RATE_LIMIT_RPM <= 0:
            return
        client_ip = request.client.host if request.client else "unknown"
        now = time.monotonic()
        window = _rate_buckets.setdefault(client_ip, [])
        # Prune entries older than 60s
        cutoff = now - 60
        _rate_buckets[client_ip] = [t for t in window if t > cutoff]
        window = _rate_buckets[client_ip]
        if len(window) >= RATE_LIMIT_RPM:
            raise HTTPException(429, "rate limit exceeded — try again later")
        window.append(now)

    # ── Metrics counters ──────────────────────────────────
    _metrics: dict[str, int | float] = {
        "requests_total": 0,
        "requests_by_method_GET": 0,
        "requests_by_method_POST": 0,
        "requests_by_method_DELETE": 0,
        "responses_2xx": 0,
        "responses_4xx": 0,
        "responses_5xx": 0,
        "publishes_total": 0,
        "bytes_uploaded_total": 0,
    }

    @app.middleware("http")
    async def metrics_middleware(request: Request, call_next):
        _metrics["requests_total"] += 1
        method_key = f"requests_by_method_{request.method}"
        if method_key in _metrics:
            _metrics[method_key] += 1
        response = await call_next(request)
        if 200 <= response.status_code < 300:
            _metrics["responses_2xx"] += 1
        elif 400 <= response.status_code < 500:
            _metrics["responses_4xx"] += 1
        elif response.status_code >= 500:
            _metrics["responses_5xx"] += 1
        return response

    # ── Landing page ──────────────────────────────────────

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def landing_page():
        from cvcpkg.server.landing import landing_html

        return HTMLResponse(landing_html())

    @app.get("/package/{name}", response_class=HTMLResponse, include_in_schema=False)
    async def package_detail_page(name: str):
        from cvcpkg.server.landing import package_detail_html

        return HTMLResponse(package_detail_html(name))

    @app.get("/guide", response_class=HTMLResponse, include_in_schema=False)
    async def guide_page():
        from cvcpkg.server.landing import guide_html

        return HTMLResponse(guide_html())

    # ── Health ──────────────────────────────────────────────

    @app.get("/healthz", response_model=HealthResponse, tags=["health"])
    async def healthz():
        state = _get_state()
        if _use_db:
            pkgs, _ = await _db_packages.get_bundles(limit=0, offset=0)
            cat = await _db_packages.get_catalog_dict()
            pkg_count = len(cat.get("bundles", []))
        else:
            pkg_count = len(state.index.get("bundles", []))
        return HealthResponse(
            version=__version__,
            storage_scheme=(
                state.storage_uri.split("://")[0] if "://" in state.storage_uri else "file"
            ),
            packages_count=pkg_count,
            uptime_seconds=round(time.monotonic() - _START_TIME, 2),
            mirror_mode=MIRROR_MODE,
        )

    # ── Admin shutdown ──────────────────────────────────────

    @app.post("/v1/admin/shutdown", tags=["admin"])
    async def admin_shutdown(
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Gracefully shut down the server.  Requires admin role."""
        if _use_db and _db_audit is not None:
            await _db_audit.record(
                action=AuditAction.token_create,  # reuse closest action
                actor=actor.name,
                target="server",
                detail="admin-initiated shutdown",
            )
        logger.info("admin shutdown requested by %s", actor.name)

        async def _do_shutdown():
            await asyncio.sleep(0.5)
            os.kill(os.getpid(), signal.SIGTERM)

        asyncio.get_event_loop().create_task(_do_shutdown())
        return {"message": "server shutting down"}

    # ── Metrics (Prometheus text format) ────────────────────

    @app.get("/metrics", tags=["health"], response_class=PlainTextResponse)
    async def prometheus_metrics():
        state = _get_state()
        if _use_db:
            cat = await _db_packages.get_catalog_dict()
            pkg_count = len(cat.get("bundles", []))
        else:
            pkg_count = len(state.index.get("bundles", []))

        uptime = round(time.monotonic() - _START_TIME, 2)
        lines = [
            "# HELP cvcpkg_up Whether the server is up (always 1).",
            "# TYPE cvcpkg_up gauge",
            "cvcpkg_up 1",
            "# HELP cvcpkg_uptime_seconds Server uptime in seconds.",
            "# TYPE cvcpkg_uptime_seconds gauge",
            f"cvcpkg_uptime_seconds {uptime}",
            "# HELP cvcpkg_packages_total Total published packages.",
            "# TYPE cvcpkg_packages_total gauge",
            f"cvcpkg_packages_total {pkg_count}",
            "# HELP cvcpkg_requests_total Total HTTP requests served.",
            "# TYPE cvcpkg_requests_total counter",
            f"cvcpkg_requests_total {_metrics['requests_total']}",
            "# HELP cvcpkg_requests_by_method HTTP requests by method.",
            "# TYPE cvcpkg_requests_by_method counter",
            f'cvcpkg_requests_by_method{{method="GET"}} {_metrics["requests_by_method_GET"]}',
            f'cvcpkg_requests_by_method{{method="POST"}} {_metrics["requests_by_method_POST"]}',
            f'cvcpkg_requests_by_method{{method="DELETE"}} {_metrics["requests_by_method_DELETE"]}',
            "# HELP cvcpkg_responses HTTP responses by status class.",
            "# TYPE cvcpkg_responses counter",
            f'cvcpkg_responses{{status="2xx"}} {_metrics["responses_2xx"]}',
            f'cvcpkg_responses{{status="4xx"}} {_metrics["responses_4xx"]}',
            f'cvcpkg_responses{{status="5xx"}} {_metrics["responses_5xx"]}',
            "# HELP cvcpkg_publishes_total Total successful publishes.",
            "# TYPE cvcpkg_publishes_total counter",
            f"cvcpkg_publishes_total {_metrics['publishes_total']}",
            "# HELP cvcpkg_bytes_uploaded_total Total bytes uploaded.",
            "# TYPE cvcpkg_bytes_uploaded_total counter",
            f"cvcpkg_bytes_uploaded_total {_metrics['bytes_uploaded_total']}",
        ]
        return PlainTextResponse(
            "\n".join(lines) + "\n",
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    # ── Catalog (read) ──────────────────────────────────────

    @app.head("/v1/catalog", tags=["catalog"])
    async def head_catalog():
        """Support HEAD requests (used by storage backends for size check)."""
        return Response(status_code=200)

    @app.get("/v1/catalog", response_model=CatalogResponse, tags=["catalog"])
    async def get_catalog(
        request: Request,
        _auth: TokenRecord | None = Depends(optional_reader_auth),
        _caller: TokenRecord | None = Depends(optional_token),
    ):
        if _use_db:
            caller = _auth or _caller
            cat = await _db_packages.get_catalog_dict(
                caller_token_name=caller.name if caller else "",
                is_admin=caller is not None and caller.role == TokenRole.admin,
            )
            revision = cat.get("revision", 0)
            bundles = cat.get("bundles", [])
        else:
            state = _get_state()
            revision = state.index.get("revision", 0)
            bundles = list(state.index.get("bundles", []))

        # Resolve relative archive_url paths (e.g. "/v1/download/…")
        # to absolute URLs so clients can download without knowing
        # the server origin separately.
        base = str(request.base_url).rstrip("/")
        for b in bundles:
            url = b.get("archive_url", "")
            if url.startswith("/"):
                b["archive_url"] = f"{base}{url}"

        return CatalogResponse(revision=revision, bundles=bundles)

    # ── Dependency graph (read) ────────────────────────────

    @app.get("/v1/deps", tags=["packages"])
    async def get_dependency_graph(
        _auth: None = Depends(optional_reader_auth),
    ):
        """Return forward and reverse dependency maps derived from recipes."""
        from cvcpkg.builder import RecipeError, find_recipes_dir, list_recipes

        try:
            recipes = list_recipes(find_recipes_dir())
        except RecipeError:
            return JSONResponse({"forward": {}, "reverse": {}})

        forward: dict[str, list[str]] = {}
        meta: dict[str, dict] = {}
        recipe_names: list[str] = []
        for r in recipes:
            recipe_block = r.raw.get("recipe", {})
            recipe_names.append(r.name)
            depends_block = r.raw.get("depends", {})
            # Prefer runtime deps (consumer-facing); fall back to build.
            raw_deps = depends_block.get("runtime", depends_block.get("build", []))
            names: list[str] = []
            for d in raw_deps:
                if isinstance(d, str):
                    names.append(d)
                elif isinstance(d, dict):
                    dep_org = d.get("org", "")
                    dep_name = d["name"]
                    names.append(f"{dep_org}/{dep_name}" if dep_org else dep_name)
            forward[r.name] = names
            meta[r.name] = {
                "description": recipe_block.get("description", ""),
                "homepage": recipe_block.get("homepage", ""),
                "license": recipe_block.get("license", ""),
                "maintainer": recipe_block.get("maintainer", ""),
                "maintainer_email": recipe_block.get("maintainer_email", ""),
                "notes": r.raw.get("notes", []) or [],
                "toolchain": r.raw.get("toolchain", {}),
            }

        reverse: dict[str, list[str]] = {}
        for pkg, deps in forward.items():
            for dep in deps:
                reverse.setdefault(dep, []).append(pkg)

        return JSONResponse(
            {
                "forward": forward,
                "reverse": reverse,
                "meta": meta,
                "recipe_names": recipe_names,
            }
        )

    # ── Recipe content (read) ──────────────────────────────

    @app.get("/v1/recipe/{name}", tags=["packages"])
    async def get_recipe(
        name: str,
        _auth: None = Depends(optional_reader_auth),
    ):
        """Return the raw recipe.yaml content for a named recipe."""
        import re

        from cvcpkg.builder import RecipeError, find_recipes_dir

        # Validate name to prevent path traversal
        if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$", name):
            return JSONResponse({"error": "invalid recipe name"}, status_code=400)

        try:
            recipes_dir = find_recipes_dir()
        except RecipeError:
            return JSONResponse({"error": "recipes not available"}, status_code=404)

        recipe_path = (recipes_dir / name / "recipe.yaml").resolve()
        # Ensure resolved path is inside recipes_dir
        if not str(recipe_path).startswith(str(recipes_dir.resolve())):
            return JSONResponse({"error": "invalid recipe name"}, status_code=400)
        if not recipe_path.is_file():
            return JSONResponse({"error": "recipe not found"}, status_code=404)

        content = recipe_path.read_text(encoding="utf-8")
        return PlainTextResponse(content, media_type="text/yaml")

    # ── Packages (read) ─────────────────────────────────────

    @app.get("/v1/packages", response_model=PackageListResponse, tags=["packages"])
    async def list_packages(
        name: str = Query("", description="Filter by component name"),
        platform: str = Query("", description="Filter by platform"),
        arch: str = Query("", description="Filter by architecture"),
        build_type: str = Query("", description="Filter by build type (release/debug)"),
        link: str = Query("", description="Filter by link mode (shared/static)"),
        recipe_version: str = Query(
            "",
            description=(
                "Filter by recipe version (chain hash).  Enables exact-match cache lookups."
            ),
        ),
        release: str = Query(
            "",
            description=(
                "Filter by release tag (e.g. 'v1.3.0').  Use 'live' to see "
                "only packages not yet in any release."
            ),
        ),
        org: str = Query("", description="Filter by organization slug"),
        search: str = Query("", description="Full-text search across all attributes"),
        include_yanked: bool = Query(False, description="Include yanked packages in results"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        _auth: None = Depends(optional_reader_auth),
    ):
        if _use_db:
            # 'live' is a virtual tag meaning release_tag == ""
            db_release = "" if release == "live" else release
            packages, total = await _db_packages.get_bundles(
                name=name,
                platform=platform,
                release=db_release,
                search=search,
                include_yanked=include_yanked,
                limit=limit,
                offset=offset,
                recipe_version=recipe_version,
                arch=arch,
                build_type=build_type,
                link=link,
                org_slug=org,
            )
            if release == "live":
                # get_bundles with release="" returns all — filter to empty tag
                packages = [p for p in packages if not p.release_tag]
                total = len(packages)
            return PackageListResponse(total=total, packages=packages)
        state = _get_state()
        bundles = state.index.get("bundles", [])
        if not include_yanked:
            bundles = [b for b in bundles if not b.get("yanked", False)]
        if name:
            bundles = [b for b in bundles if b.get("name") == name]
        if platform:
            bundles = [b for b in bundles if b.get("platform") == platform]
        if arch:
            bundles = [b for b in bundles if b.get("arch") == arch]
        if build_type:
            bundles = [b for b in bundles if b.get("build_type") == build_type]
        if link:
            bundles = [b for b in bundles if b.get("link") == link]
        if recipe_version:
            bundles = [b for b in bundles if b.get("recipe_version") == recipe_version]
        if org:
            bundles = [b for b in bundles if b.get("org") == org]
        if release == "live":
            bundles = [b for b in bundles if not b.get("release_tag")]
        elif release:
            bundles = [b for b in bundles if b.get("release_tag") == release]
        total = len(bundles)
        page = bundles[offset : offset + limit]
        # Build a lookup for publisher emails
        _pub_emails: dict[str, str] = {}
        for b in page:
            pub = b.get("published_by", "")
            if pub and pub not in _pub_emails:
                rec = state.tokens.get_public_profile(pub)
                _pub_emails[pub] = rec.email if rec else ""
        packages = [
            PackageInfo(
                name=b["name"],
                version=b["version"],
                platform=b.get("platform", ""),
                arch=b.get("arch", ""),
                build_type=b.get("build_type", ""),
                link=b.get("link", ""),
                sha256=b.get("sha256", ""),
                size_bytes=b.get("size_bytes", 0),
                archive_url=b.get("archive_url", ""),
                published_at=b.get("published_at", "1970-01-01T00:00:00+00:00"),
                yanked=b.get("yanked", False),
                published_by=b.get("published_by", ""),
                published_by_email=_pub_emails.get(b.get("published_by", ""), ""),
                org=b.get("org", ""),
            )
            for b in page
        ]
        return PackageListResponse(total=total, packages=packages)

    @app.get("/v1/packages/{name}", response_model=PackageListResponse, tags=["packages"])
    async def get_package(
        name: str,
        include_yanked: bool = Query(False, description="Include yanked packages in results"),
        _auth: None = Depends(optional_reader_auth),
    ):
        if _use_db:
            packages, total = await _db_packages.get_bundles(
                name=name, include_yanked=include_yanked
            )
            return PackageListResponse(total=total, packages=packages)
        state = _get_state()
        bundles = [b for b in state.index.get("bundles", []) if b.get("name") == name]
        if not include_yanked:
            bundles = [b for b in bundles if not b.get("yanked", False)]
        packages = [
            PackageInfo(
                name=b["name"],
                version=b["version"],
                platform=b.get("platform", ""),
                arch=b.get("arch", ""),
                build_type=b.get("build_type", ""),
                link=b.get("link", ""),
                sha256=b.get("sha256", ""),
                size_bytes=b.get("size_bytes", 0),
                archive_url=b.get("archive_url", ""),
                published_at=b.get("published_at", "1970-01-01T00:00:00+00:00"),
                yanked=b.get("yanked", False),
                published_by=b.get("published_by", ""),
                org=b.get("org", ""),
            )
            for b in bundles
        ]
        return PackageListResponse(total=len(packages), packages=packages)

    # ── Cache status probe ──────────────────────────────────

    @app.get(
        "/v1/cache/status",
        response_model=CacheStatusResponse,
        tags=["cache"],
    )
    async def cache_status(
        name: str = Query(..., description="Component name"),
        chain_hash: str = Query(..., description="Chain hash (recipe version)"),
        platform: str = Query(..., description="Target platform"),
        arch: str = Query("", description="Architecture"),
        build_type: str = Query("", description="Build type (release/debug)"),
        link: str = Query("", description="Link mode (shared/static)"),
        org: str = Query("", description="Organization slug"),
        authorization: str | None = Header(None),
    ):
        miss = CacheStatusResponse(hit=False)

        # Private-org ACL: require auth + membership
        if org and _use_db and _db_orgs is not None:
            org_info = await _db_orgs.get(org)
            if org_info is None:
                return miss
            if org_info.is_private:
                actor = await optional_token(authorization)
                if actor is None or not await _db_orgs.is_member(org, actor.name):
                    raise HTTPException(403, f"authentication required for private org '{org}'")

        if _use_db:
            packages, _total = await _db_packages.get_bundles(
                name=name,
                platform=platform,
                recipe_version=chain_hash,
                arch=arch,
                build_type=build_type,
                link=link,
                org_slug=org,
                release="",
                search="",
                include_yanked=False,
                limit=1,
                offset=0,
            )
            if packages:
                p = packages[0]
                return CacheStatusResponse(
                    hit=True,
                    name=p.name,
                    version=p.version,
                    chain_hash=chain_hash,
                    platform=p.platform,
                    arch=p.arch,
                    build_type=p.build_type,
                    link=p.link,
                    archive_url=p.archive_url,
                    sha256=p.sha256,
                    size_bytes=p.size_bytes,
                    org=p.org,
                    published_at=p.published_at,
                )
            return miss

        # YAML / in-memory path
        state = _get_state()
        for b in state.index.get("bundles", []):
            if b.get("yanked", False):
                continue
            if b.get("name") != name or b.get("platform") != platform:
                continue
            if b.get("recipe_version") != chain_hash:
                continue
            if arch and b.get("arch") != arch:
                continue
            if build_type and b.get("build_type") != build_type:
                continue
            if link and b.get("link") != link:
                continue
            if org and b.get("org") != org:
                continue
            return CacheStatusResponse(
                hit=True,
                name=b["name"],
                version=b["version"],
                chain_hash=chain_hash,
                platform=b.get("platform", ""),
                arch=b.get("arch", ""),
                build_type=b.get("build_type", ""),
                link=b.get("link", ""),
                archive_url=b.get("archive_url", ""),
                sha256=b.get("sha256", ""),
                size_bytes=b.get("size_bytes", 0),
                org=b.get("org", ""),
                published_at=b.get("published_at"),
            )
        return miss

    # ── Cache listing, stats & GC ──────────────────────────────

    @app.get("/v1/cache", response_model=PackageListResponse, tags=["cache"])
    async def list_cache_entries(
        name: str = Query("", description="Filter by component name"),
        platform: str = Query("", description="Filter by platform"),
        arch: str = Query("", description="Filter by architecture"),
        older_than: str = Query(
            "",
            description="Age filter, e.g. '14d' for 14 days",
        ),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """List non-release cache entries (packages with empty release_tag)."""
        if _use_db:
            packages, total = await _db_packages.get_bundles(
                name=name,
                platform=platform,
                arch=arch,
                release="",
                include_yanked=False,
                limit=limit,
                offset=offset,
            )
            # get_bundles with release="" returns ALL; filter to empty release_tag
            packages = [p for p in packages if not p.release_tag]
            total = len(packages)
        else:
            state = _get_state()
            bundles = state.index.get("bundles", [])
            bundles = [b for b in bundles if not b.get("yanked") and not b.get("release_tag")]
            if name:
                bundles = [b for b in bundles if b.get("name") == name]
            if platform:
                bundles = [b for b in bundles if b.get("platform") == platform]
            if arch:
                bundles = [b for b in bundles if b.get("arch") == arch]
            total = len(bundles)
            page = bundles[offset : offset + limit]
            packages = [
                PackageInfo(
                    name=b["name"],
                    version=b["version"],
                    platform=b.get("platform", ""),
                    arch=b.get("arch", ""),
                    build_type=b.get("build_type", ""),
                    link=b.get("link", ""),
                    sha256=b.get("sha256", ""),
                    size_bytes=b.get("size_bytes", 0),
                    archive_url=b.get("archive_url", ""),
                    published_at=b.get("published_at", "1970-01-01T00:00:00+00:00"),
                    org=b.get("org", ""),
                )
                for b in page
            ]
        return PackageListResponse(total=total, packages=packages)

    @app.delete("/v1/cache", tags=["cache"])
    async def delete_cache_entries(
        request: Request,
        older_than: str = Query(
            "",
            description="Delete entries older than this duration (e.g. '14d')",
        ),
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Bulk-purge non-release cache entries (admin-only).

        Use ``older_than`` to restrict deletion by age.  Without a filter
        all non-release packages are removed.
        """
        if not _use_db:
            raise HTTPException(501, "Bulk cache deletion requires database backend")

        deleted: list[dict] = []
        if older_than:
            secs = _parse_duration(older_than)
            deleted = await _db_packages.gc_by_age(secs)
        else:
            # Delete all non-release packages (max_storage_bytes=0 evicts
            # everything down to zero).
            deleted = await _db_packages.gc_by_storage(0)

        # Update org storage counters.
        org_deltas: dict[str, int] = {}
        for d in deleted:
            slug = d.get("org_slug", "")
            if slug and _db_orgs is not None:
                org_deltas[slug] = org_deltas.get(slug, 0) + d["size_bytes"]
        for slug, delta in org_deltas.items():
            await _db_orgs.update_storage_used(slug, -delta)

        await _db_audit.record(
            action=AuditAction.cache_gc,
            actor=actor.name,
            target="cache",
            detail=f"bulk_delete deleted={len(deleted)} older_than={older_than!r}",
        )
        return {"deleted_count": len(deleted), "deleted": deleted}

    @app.get("/v1/cache/stats", tags=["cache"])
    async def cache_stats(
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Return storage statistics: total, per-org, and package counts."""
        if not _use_db:
            # YAML backend — simple stats
            state = _get_state()
            bundles = [b for b in state.index.get("bundles", []) if not b.get("yanked")]
            total_bytes = sum(b.get("size_bytes", 0) for b in bundles)
            return {
                "total_packages": len(bundles),
                "total_size_bytes": total_bytes,
                "orgs": {},
            }
        return await _db_packages.cache_stats()

    @app.post("/v1/cache/gc", tags=["cache"])
    async def cache_gc(
        request: Request,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Run garbage collection on cached packages (admin-only).

        Accepts a JSON body with one or more of:
        - ``max_age_seconds``: delete non-release packages older than this
        - ``max_storage_bytes``: evict oldest non-release packages to fit
        - ``valid_chain_hashes``: list of current chain hashes; entries
          whose ``recipe_version`` is not in this set are stale and removed
        """
        if not _use_db:
            raise HTTPException(501, "GC requires database backend")

        body = await request.json()
        deleted: list[dict] = []

        if "max_age_seconds" in body:
            age = float(body["max_age_seconds"])
            if age <= 0:
                raise HTTPException(422, "max_age_seconds must be > 0")
            deleted.extend(await _db_packages.gc_by_age(age))

        if "max_storage_bytes" in body:
            cap = int(body["max_storage_bytes"])
            if cap < 0:
                raise HTTPException(422, "max_storage_bytes must be >= 0")
            deleted.extend(await _db_packages.gc_by_storage(cap))

        if "valid_chain_hashes" in body:
            hashes = body["valid_chain_hashes"]
            if not isinstance(hashes, list):
                raise HTTPException(422, "valid_chain_hashes must be a list of strings")
            deleted.extend(await _db_packages.gc_by_staleness(set(hashes)))

        if not body or not any(
            k in body for k in ("max_age_seconds", "max_storage_bytes", "valid_chain_hashes")
        ):
            raise HTTPException(
                422, "specify max_age_seconds, max_storage_bytes, and/or valid_chain_hashes"
            )

        # Update org storage counters for deleted packages.
        org_deltas: dict[str, int] = {}
        for d in deleted:
            org_slug = d.get("org_slug", "")
            if org_slug and _db_orgs is not None:
                org_deltas[org_slug] = org_deltas.get(org_slug, 0) + d["size_bytes"]
        for org_slug, delta in org_deltas.items():
            await _db_orgs.update_storage_used(org_slug, -delta)

        await _db_audit.record(
            action=AuditAction.cache_gc,
            actor=actor.name,
            target="cache",
            detail=f"deleted={len(deleted)} params={body}",
        )
        return {
            "deleted_count": len(deleted),
            "deleted": deleted,
        }

    # ── Download (read) ─────────────────────────────────────

    @app.head("/v1/download/{filename}", tags=["packages"])
    async def head_download_archive(
        filename: str,
        _auth: None = Depends(optional_reader_auth),
    ):
        """HEAD support so clients can check size before downloading."""
        state = _get_state()
        safe_name = Path(filename).name
        archive_path = state.archives_dir() / safe_name
        if not archive_path.is_file():
            raise HTTPException(404, f"archive not found: {safe_name}")
        return Response(
            status_code=200,
            headers={
                "Content-Length": str(archive_path.stat().st_size),
                "Content-Type": "application/octet-stream",
            },
        )

    @app.get("/v1/download/{filename}", tags=["packages"])
    async def download_archive(
        filename: str,
        _auth: None = Depends(optional_reader_auth),
    ):
        state = _get_state()
        # Sanitise filename to prevent path traversal
        safe_name = Path(filename).name
        archive_path = state.archives_dir() / safe_name
        if not archive_path.is_file():
            raise HTTPException(404, f"archive not found: {safe_name}")

        # Record download event for analytics
        if _use_db and _db_downloads is not None:
            # Parse package name/version/platform from filename
            # Format: {name}-{version}-{platform}-{arch}-{build_type}[-{link}].tar.zst
            parts = safe_name.rsplit(".", 2)  # strip .tar.zst or similar
            stem = parts[0] if parts else safe_name
            # Best-effort extraction — look up in DB for exact match
            pkg_name = stem.split("-")[0] if "-" in stem else stem
            pkg_version = ""
            pkg_platform = ""
            if _db_packages is not None:
                # Try to find the package by archive_url for accurate metadata
                pkgs, _ = await _db_packages.get_bundles(limit=1000)
                for p in pkgs:
                    if p.archive_url.endswith(f"/{safe_name}"):
                        pkg_name = p.name
                        pkg_version = p.version
                        pkg_platform = p.platform
                        break
            await _db_downloads.record(pkg_name, pkg_version, pkg_platform)

        def _stream():
            with open(archive_path, "rb") as f:
                while chunk := f.read(1 << 16):
                    yield chunk

        return StreamingResponse(
            _stream(),
            media_type="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{safe_name}"',
                "Content-Length": str(archive_path.stat().st_size),
            },
        )

    # ── Publish (write) ─────────────────────────────────────

    @app.post("/v1/publish", response_model=PublishResponse, tags=["publish"])
    async def publish(
        request: Request,
        file: UploadFile,
        name: str = Query(..., description="Component name"),
        version: str = Query(..., description="Component version"),
        platform: str = Query("", description="Target platform"),
        arch: str = Query("", description="Target architecture"),
        build_type: str = Query("release"),
        link: str = Query("shared"),
        signature: str = Query("", description="Base64url Ed25519 signature"),
        key_fingerprint: str = Query("", description="SHA-256 fingerprint of signing key"),
        release_tag: str = Query(
            "",
            description=(
                "cvcpkg release tag (e.g. 'v1.3.0').  Leave empty for live/updated builds."
            ),
        ),
        recipe_version: str = Query(
            "",
            description=("Recipe revision that produced this build (commit SHA or recipe hash)."),
        ),
        description: str = Query("", description="Short description of the component"),
        homepage: str = Query("", description="Upstream project homepage URL"),
        pkg_license: str = Query("", alias="license", description="SPDX license identifier"),
        maintainer: str = Query("", description="Package maintainer"),
        pkg_tags: str = Query("", alias="tags", description="Comma-separated tags"),
        org: str = Query(
            "",
            description="Organization slug. Empty for official/public packages.",
        ),
        required_deps: str = Query(
            "[]",
            description="JSON-encoded list of runtime dependency dicts [{name, version}, ...]",
        ),
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        if MIRROR_MODE:
            raise HTTPException(
                403,
                "this server is running in mirror mode and does not accept publishes",
            )
        _check_rate_limit(request)
        state = _get_state()

        # Validate org slug format
        if org:
            from cvcpkg.server.models import validate_org_slug

            err = validate_org_slug(org)
            if err:
                raise HTTPException(422, err)

        # Validate org membership and storage limit if publishing to an org
        if org and _use_db and _db_orgs is not None:
            org_info = await _db_orgs.get(org)
            if org_info is None:
                raise HTTPException(404, f"organization '{org}' not found")
            if not await _db_orgs.is_member(org, actor.name):
                raise HTTPException(403, f"you are not a member of organization '{org}'")

        # Auto-create stub tag rows before the duplicate check so tags
        # are populated even when the package already exists.
        if _use_db and pkg_tags and _db_tags is not None:
            await _db_tags.ensure_tags(tags_csv=pkg_tags, org_slug=org, created_by=actor.name)

        # Check for duplicates
        if _use_db:
            if await _db_packages.check_duplicate(name, version, platform, arch, build_type, link):
                raise HTTPException(
                    409,
                    f"{name}=={version} ({platform}/{arch}/{build_type}/{link}) already published. "
                    "Yank the existing version first, or use a new revision.",
                )
        else:
            for b in state.index.get("bundles", []):
                if (
                    b["name"] == name
                    and b["version"] == version
                    and b.get("platform") == platform
                    and b.get("arch") == arch
                    and b.get("build_type") == build_type
                    and b.get("link") == link
                ):
                    raise HTTPException(
                        409,
                        f"{name}=={version} ({platform}/{arch}/{build_type}"
                        f"/{link}) already published. "
                        "Yank the existing version first, or use a new revision.",
                    )

        # Read and hash the upload — stream to disk to avoid holding
        # large archives in memory.
        h = hashlib.sha256()
        total_size = 0
        safe_filename = f"{name}-{version}-{platform}-{arch}-{build_type}-{link}.tar.zst"
        safe_filename = "".join(
            c if c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-+" else "_"
            for c in safe_filename
        )
        dest = state.archives_dir() / safe_filename

        # Write to temp file in the same directory, then rename for atomicity
        fd, tmp_path_str = tempfile.mkstemp(dir=state.archives_dir(), suffix=".upload")
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "wb") as tmp_f:
                while True:
                    chunk = await file.read(1 << 16)  # 64 KiB chunks
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            413,
                            f"upload exceeds maximum size of {MAX_UPLOAD_BYTES} bytes",
                        )
                    h.update(chunk)
                    tmp_f.write(chunk)
            tmp_path.rename(dest)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

        sha256 = h.hexdigest()
        size_bytes = total_size

        # Update the index
        import datetime

        archive_url = f"/v1/download/{safe_filename}"

        if _use_db:
            # Check global cache storage limit
            if GLOBAL_CACHE_STORAGE_LIMIT_BYTES > 0:
                total_used = await _db_packages.total_storage_bytes()
                if total_used + size_bytes > GLOBAL_CACHE_STORAGE_LIMIT_BYTES:
                    dest.unlink(missing_ok=True)
                    raise HTTPException(413, "global cache storage limit exceeded")

            # Check org storage limit before registering
            if org and _db_orgs is not None:
                if not await _db_orgs.check_storage_limit(org, size_bytes):
                    dest.unlink(missing_ok=True)
                    raise HTTPException(413, f"organization '{org}' storage limit exceeded")

            await _db_packages.add_package(
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
                pkg_license=pkg_license,
                maintainer=maintainer,
                tags=pkg_tags,
                org_slug=org,
                published_by=actor.name,
                required_deps=required_deps,
            )

            # Track org storage usage
            if org and _db_orgs is not None:
                await _db_orgs.update_storage_used(org, size_bytes)

            await _db_audit.record(
                action=AuditAction.publish,
                actor=actor.name,
                target=f"{name}=={version}",
                detail=(
                    f"platform={platform} arch={arch}"
                    f" sha256={sha256} release={release_tag or 'live'}"
                    f" org={org or 'public'}"
                ),
            )
        else:
            bundle = {
                "name": name,
                "version": version,
                "platform": platform,
                "arch": arch,
                "build_type": build_type,
                "link": link,
                "sha256": sha256,
                "size_bytes": size_bytes,
                "archive_url": archive_url,
                "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "yanked": False,
                "signature": signature,
                "key_fingerprint": key_fingerprint,
                "release_tag": release_tag,
                "recipe_version": recipe_version,
                "published_by": actor.name,
                "org": org,
                "required_deps": json.loads(required_deps),
            }
            state.index.setdefault("bundles", []).append(bundle)
            state.save_index()
            state.audit.record(
                action=AuditAction.publish,
                actor=actor.name,
                target=f"{name}=={version}",
                detail=(
                    f"platform={platform} arch={arch}"
                    f" sha256={sha256} release={release_tag or 'live'}"
                ),
            )

        _metrics["publishes_total"] += 1
        _metrics["bytes_uploaded_total"] += size_bytes

        await emit_webhook_event(
            "package.published",
            {
                "name": name,
                "version": version,
                "platform": platform,
                "arch": arch,
                "org": org or "",
            },
            org_slug=org or "",
        )

        return PublishResponse(
            name=name,
            version=version,
            sha256=sha256,
            archive_url=archive_url,
        )

    # ── Chunked / resumable upload ──────────────────────────

    @app.post("/v1/upload/init", tags=["upload"])
    async def upload_init(
        request: Request,
        name: str = Query(..., description="Component name"),
        version: str = Query(..., description="Component version"),
        platform: str = Query("", description="Target platform"),
        arch: str = Query("", description="Target architecture"),
        build_type: str = Query("release"),
        link: str = Query("shared"),
        total_size: int = Query(0, description="Total file size in bytes (0 = unknown)"),
        signature: str = Query(""),
        key_fingerprint: str = Query(""),
        release_tag: str = Query(""),
        recipe_version: str = Query(""),
        description: str = Query(""),
        homepage: str = Query(""),
        pkg_license: str = Query("", alias="license"),
        maintainer: str = Query(""),
        pkg_tags: str = Query("", alias="tags"),
        required_deps: str = Query("[]", description="JSON-encoded runtime deps"),
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Initialise a chunked upload session.

        Returns an ``upload_id`` and recommended ``chunk_size``.
        Upload chunks with ``PATCH /v1/upload/{upload_id}``, then
        finalise with ``POST /v1/upload/{upload_id}/complete``.
        """
        if MIRROR_MODE:
            raise HTTPException(
                403,
                "this server is running in mirror mode and does not accept publishes",
            )
        _check_rate_limit(request)
        _purge_expired_sessions()
        state = _get_state()

        if total_size > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413, f"declared size {total_size} exceeds maximum {MAX_UPLOAD_BYTES} bytes"
            )

        # Auto-create stub tag rows before the duplicate check so tags
        # are populated even when the package already exists.
        if _use_db and pkg_tags and _db_tags is not None:
            await _db_tags.ensure_tags(tags_csv=pkg_tags, org_slug="", created_by=actor.name)

        # Check for duplicates
        if _use_db:
            if await _db_packages.check_duplicate(name, version, platform, arch, build_type, link):
                raise HTTPException(
                    409,
                    f"{name}=={version} ({platform}/{arch}/{build_type}/{link}) already published.",
                )
        else:
            for b in state.index.get("bundles", []):
                if (
                    b["name"] == name
                    and b["version"] == version
                    and b.get("platform") == platform
                    and b.get("arch") == arch
                    and b.get("build_type") == build_type
                    and b.get("link") == link
                ):
                    raise HTTPException(
                        409,
                        f"{name}=={version} ({platform}/{arch}/{build_type}"
                        f"/{link}) already published.",
                    )

        upload_id = secrets.token_urlsafe(32)
        fd, tmp_path_str = tempfile.mkstemp(dir=state.archives_dir(), suffix=".chunk")
        os.close(fd)
        tmp_path = Path(tmp_path_str)

        _upload_sessions[upload_id] = UploadSession(
            upload_id=upload_id,
            name=name,
            version=version,
            platform=platform,
            arch=arch,
            build_type=build_type,
            link=link,
            signature=signature,
            key_fingerprint=key_fingerprint,
            release_tag=release_tag,
            recipe_version=recipe_version,
            description=description,
            homepage=homepage,
            pkg_license=pkg_license,
            maintainer=maintainer,
            tags=pkg_tags,
            required_deps=required_deps,
            actor_name=actor.name,
            temp_path=tmp_path,
            total_size=total_size,
        )

        return JSONResponse(
            {
                "upload_id": upload_id,
                "chunk_size": CHUNK_SIZE,
                "max_size": MAX_UPLOAD_BYTES,
                "expires_in": UPLOAD_SESSION_TTL,
            },
            status_code=201,
        )

    @app.get("/v1/upload/{upload_id}", tags=["upload"])
    async def upload_status(
        upload_id: str,
        _actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Return current status of a chunked upload (bytes received so far)."""
        session = _upload_sessions.get(upload_id)
        if not session:
            raise HTTPException(404, "upload session not found or expired")
        return {
            "upload_id": upload_id,
            "bytes_received": session.bytes_received,
            "total_size": session.total_size,
            "name": session.name,
            "version": session.version,
        }

    @app.patch("/v1/upload/{upload_id}", tags=["upload"])
    async def upload_chunk(
        upload_id: str,
        request: Request,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Append a chunk to an in-progress upload.

        Send the raw chunk bytes as the request body
        (``Content-Type: application/octet-stream``).
        Include ``Content-Range: bytes {start}-{end}/{total}``
        for resume safety — the server verifies that ``start``
        matches ``bytes_received``.
        """
        session = _upload_sessions.get(upload_id)
        if not session:
            raise HTTPException(404, "upload session not found or expired")
        if session.actor_name != actor.name:
            raise HTTPException(403, "upload session belongs to a different actor")

        # Parse Content-Range if provided for resume verification
        content_range = request.headers.get("content-range", "")
        if content_range:
            # Format: bytes start-end/total
            try:
                range_spec = content_range.split(" ", 1)[1]  # drop "bytes"
                range_part, total_part = range_spec.split("/")
                range_start = int(range_part.split("-")[0])
            except (IndexError, ValueError) as exc:
                raise HTTPException(400, f"malformed Content-Range: {content_range}") from exc
            if range_start != session.bytes_received:
                raise HTTPException(
                    409,
                    f"offset mismatch: server has {session.bytes_received} bytes, "
                    f"chunk starts at {range_start}. Resume from {session.bytes_received}.",
                )

        # Stream chunk to disk
        chunk_bytes = 0
        with open(session.temp_path, "ab") as f:
            async for body_chunk in request.stream():
                chunk_bytes += len(body_chunk)
                if session.bytes_received + chunk_bytes > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        413,
                        f"upload would exceed maximum size of {MAX_UPLOAD_BYTES} bytes",
                    )
                session.hasher.update(body_chunk)
                f.write(body_chunk)

        session.bytes_received += chunk_bytes

        return {
            "upload_id": upload_id,
            "bytes_received": session.bytes_received,
            "total_size": session.total_size,
        }

    @app.post("/v1/upload/{upload_id}/complete", response_model=PublishResponse, tags=["upload"])
    async def upload_complete(
        upload_id: str,
        request: Request,
        expected_sha256: str = Query("", description="Expected SHA-256 for verification"),
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Finalise a chunked upload — verify integrity and register the package."""
        _check_rate_limit(request)
        session = _upload_sessions.get(upload_id)
        if not session:
            raise HTTPException(404, "upload session not found or expired")
        if session.actor_name != actor.name:
            raise HTTPException(403, "upload session belongs to a different actor")
        if session.bytes_received == 0:
            raise HTTPException(400, "no data uploaded")

        sha256 = session.hasher.hexdigest()
        if expected_sha256 and sha256 != expected_sha256:
            # Integrity mismatch — discard
            session.temp_path.unlink(missing_ok=True)
            _upload_sessions.pop(upload_id, None)
            raise HTTPException(
                422,
                f"SHA-256 mismatch: expected {expected_sha256}, got {sha256}",
            )

        state = _get_state()

        # Build safe filename and move temp → final
        safe_filename = (
            f"{session.name}-{session.version}-{session.platform}-{session.arch}"
            f"-{session.build_type}-{session.link}.tar.zst"
        )
        safe_filename = "".join(
            c if c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-+" else "_"
            for c in safe_filename
        )
        dest = state.archives_dir() / safe_filename
        session.temp_path.rename(dest)

        archive_url = f"/v1/download/{safe_filename}"
        size_bytes = session.bytes_received

        import datetime

        if _use_db:
            await _db_packages.add_package(
                name=session.name,
                version=session.version,
                platform=session.platform,
                arch=session.arch,
                build_type=session.build_type,
                link=session.link,
                sha256=sha256,
                size_bytes=size_bytes,
                archive_url=archive_url,
                signature=session.signature,
                key_fingerprint=session.key_fingerprint,
                release_tag=session.release_tag,
                recipe_version=session.recipe_version,
                description=session.description,
                homepage=session.homepage,
                pkg_license=session.pkg_license,
                maintainer=session.maintainer,
                tags=session.tags,
                published_by=actor.name,
                required_deps=session.required_deps,
            )

            await _db_audit.record(
                action=AuditAction.publish,
                actor=actor.name,
                target=f"{session.name}=={session.version}",
                detail=(
                    f"platform={session.platform} arch={session.arch} "
                    f"sha256={sha256} release={session.release_tag or 'live'} chunked=yes"
                ),
            )
        else:
            bundle = {
                "name": session.name,
                "version": session.version,
                "platform": session.platform,
                "arch": session.arch,
                "build_type": session.build_type,
                "link": session.link,
                "sha256": sha256,
                "size_bytes": size_bytes,
                "archive_url": archive_url,
                "published_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "yanked": False,
                "signature": session.signature,
                "key_fingerprint": session.key_fingerprint,
                "release_tag": session.release_tag,
                "recipe_version": session.recipe_version,
                "published_by": actor.name,
                "required_deps": json.loads(session.required_deps),
            }
            state.index.setdefault("bundles", []).append(bundle)
            state.save_index()
            state.audit.record(
                action=AuditAction.publish,
                actor=actor.name,
                target=f"{session.name}=={session.version}",
                detail=(
                    f"platform={session.platform} arch={session.arch} "
                    f"sha256={sha256} release={session.release_tag or 'live'} chunked=yes"
                ),
            )

        _metrics["publishes_total"] += 1
        _metrics["bytes_uploaded_total"] += size_bytes
        _upload_sessions.pop(upload_id, None)

        await emit_webhook_event(
            "package.published",
            {
                "name": session.name,
                "version": session.version,
                "platform": session.platform,
                "arch": session.arch,
                "org": getattr(session, "org", "") or "",
            },
        )

        return PublishResponse(
            name=session.name,
            version=session.version,
            sha256=sha256,
            archive_url=archive_url,
        )

    @app.delete("/v1/upload/{upload_id}", tags=["upload"], status_code=204)
    async def upload_cancel(
        upload_id: str,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Cancel and discard an in-progress upload session."""
        session = _upload_sessions.get(upload_id)
        if not session:
            raise HTTPException(404, "upload session not found or expired")
        if session.actor_name != actor.name:
            raise HTTPException(403, "upload session belongs to a different actor")
        session.temp_path.unlink(missing_ok=True)
        _upload_sessions.pop(upload_id, None)

    # ── Yank / Unyank (write) ──────────────────────────────

    @app.post("/v1/packages/{name}/{version}/yank", tags=["publish"])
    async def yank(
        name: str,
        version: str,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        if _use_db:
            await _db_packages.yank(name, version)
            await _db_audit.record(
                action=AuditAction.yank,
                actor=actor.name,
                target=f"{name}=={version}",
            )
        else:
            state = _get_state()
            for b in state.index.get("bundles", []):
                if b["name"] == name and b["version"] == version:
                    b["yanked"] = True
            state.save_index()
            state.audit.record(
                action=AuditAction.yank,
                actor=actor.name,
                target=f"{name}=={version}",
            )
        return {"message": f"yanked {name}=={version}"}

    @app.post("/v1/packages/{name}/{version}/unyank", tags=["publish"])
    async def unyank(
        name: str,
        version: str,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        if _use_db:
            await _db_packages.unyank(name, version)
            await _db_audit.record(
                action=AuditAction.unyank,
                actor=actor.name,
                target=f"{name}=={version}",
            )
        else:
            state = _get_state()
            for b in state.index.get("bundles", []):
                if b["name"] == name and b["version"] == version:
                    b["yanked"] = False
            state.save_index()
            state.audit.record(
                action=AuditAction.unyank,
                actor=actor.name,
                target=f"{name}=={version}",
            )
        return {"message": f"unyanked {name}=={version}"}

    # ── Delete (admin only) ─────────────────────────────────

    @app.delete("/v1/packages/{name}/{version}", tags=["publish"])
    async def delete_package(
        name: str,
        version: str,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        if _use_db:
            removed = await _db_packages.delete(name, version)
            if removed == 0:
                raise HTTPException(404, f"{name}=={version} not found")
            await _db_audit.record(
                action=AuditAction.delete,
                actor=actor.name,
                target=f"{name}=={version}",
            )
            return {"message": f"deleted {name}=={version}", "removed": removed}
        state = _get_state()
        before = len(state.index.get("bundles", []))
        state.index["bundles"] = [
            b
            for b in state.index.get("bundles", [])
            if not (b["name"] == name and b["version"] == version)
        ]
        after = len(state.index["bundles"])
        if before == after:
            raise HTTPException(404, f"{name}=={version} not found")
        state.save_index()
        state.audit.record(
            action=AuditAction.delete,
            actor=actor.name,
            target=f"{name}=={version}",
        )
        return {"message": f"deleted {name}=={version}", "removed": before - after}

    # ── Token management (admin) ────────────────────────────

    @app.post("/v1/tokens", response_model=TokenCreateResponse, tags=["tokens"])
    async def create_token(
        req: TokenCreateRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        if not _is_valid_identifier(req.name):
            raise HTTPException(
                422,
                f"invalid token name '{req.name}': must be a valid C identifier "
                "(start with a letter or underscore, followed by"
                " letters, digits, underscores, or hyphens)",
            )
        state = _get_state()
        try:
            if _use_db:
                raw = await _db_tokens.create(
                    name=req.name,
                    role=req.role,
                    expires_in_days=req.expires_in_days,
                    email=req.email,
                    description=req.description,
                    metadata=req.metadata,
                )
            else:
                raw = state.tokens.create(
                    name=req.name,
                    role=req.role,
                    expires_in_days=req.expires_in_days,
                    email=req.email,
                    description=req.description,
                    metadata=req.metadata,
                )
        except ValueError:
            raise HTTPException(
                409,
                f"token name '{req.name}' is already taken",
            ) from None
        if _use_db:
            await _db_audit.record(
                action=AuditAction.token_create,
                actor=actor.name,
                target=req.name,
                detail=f"role={req.role.value}",
            )
            record = await _db_tokens.verify(raw)
        else:
            state.audit.record(
                action=AuditAction.token_create,
                actor=actor.name,
                target=req.name,
                detail=f"role={req.role.value}",
            )
            record = state.tokens.verify(raw)
        return TokenCreateResponse(
            name=req.name,
            role=req.role,
            token=raw,
            expires_at=record.expires_at if record else None,
        )

    @app.delete("/v1/tokens/{name}", tags=["tokens"])
    async def revoke_token(
        name: str,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        state = _get_state()
        if _use_db:
            if not await _db_tokens.revoke(name):
                raise HTTPException(404, f"token '{name}' not found")
            await _db_audit.record(
                action=AuditAction.token_revoke,
                actor=actor.name,
                target=name,
            )
        else:
            if not state.tokens.revoke(name):
                raise HTTPException(404, f"token '{name}' not found")
            state.audit.record(
                action=AuditAction.token_revoke,
                actor=actor.name,
                target=name,
            )
        return {"message": f"revoked token '{name}'"}

    @app.get("/v1/tokens", tags=["tokens"])
    async def list_tokens(
        _actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        if _use_db:
            tokens = await _db_tokens.list_tokens()
        else:
            state = _get_state()
            tokens = state.tokens.list_tokens()
        return {
            "tokens": [
                {
                    "name": t.name,
                    "role": t.role.value,
                    "email": t.email,
                    "description": t.description,
                    "metadata": t.metadata,
                    "created_at": t.created_at.isoformat(),
                    "expires_at": t.expires_at.isoformat() if t.expires_at else None,
                    "revoked": t.revoked,
                }
                for t in tokens
            ]
        }

    @app.patch("/v1/tokens/{name}/email", tags=["tokens"])
    async def update_token_email(
        name: str,
        req: EmailUpdateRequest,
        authorization: str | None = Header(None),
    ):
        """Update a token's email.

        Admins can update any token's email.  Non-admin users can only
        update their own token's email.
        """
        state = _get_state()
        raw = _extract_token(authorization)
        if raw is None:
            raise HTTPException(401, "missing Authorization header")
        if _use_db:
            actor = await _db_tokens.verify(raw)
        else:
            actor = state.tokens.verify(raw)
        if actor is None:
            raise HTTPException(401, "invalid or expired token")
        # Non-admins can only update their own email
        if actor.role != TokenRole.admin and actor.name != name:
            raise HTTPException(403, "you can only update your own token's email")

        if _use_db:
            if not await _db_tokens.update_email(name, req.email):
                raise HTTPException(404, f"token '{name}' not found")
            await _db_audit.record(
                action=AuditAction.token_update_email,
                actor=actor.name,
                target=name,
                detail=f"email={req.email}",
            )
        else:
            if not state.tokens.update_email(name, req.email):
                raise HTTPException(404, f"token '{name}' not found")
            state.audit.record(
                action=AuditAction.token_update_email,
                actor=actor.name,
                target=name,
                detail=f"email={req.email}",
            )
        return {"message": f"email for '{name}' updated"}

    @app.patch("/v1/tokens/{name}/profile", tags=["tokens"])
    async def update_token_profile(
        name: str,
        req: ProfileUpdateRequest,
        authorization: str | None = Header(None),
    ):
        """Update a token's profile (description and/or metadata).

        Admins can update any token's profile.  Non-admin users can only
        update their own.
        """
        state = _get_state()
        raw = _extract_token(authorization)
        if raw is None:
            raise HTTPException(401, "missing Authorization header")
        if _use_db:
            actor = await _db_tokens.verify(raw)
        else:
            actor = state.tokens.verify(raw)
        if actor is None:
            raise HTTPException(401, "invalid or expired token")
        if actor.role != TokenRole.admin and actor.name != name:
            raise HTTPException(403, "you can only update your own profile")

        if _use_db:
            if not await _db_tokens.update_profile(
                name, description=req.description, metadata=req.metadata
            ):
                raise HTTPException(404, f"token '{name}' not found")
            await _db_audit.record(
                action=AuditAction.token_update_profile,
                actor=actor.name,
                target=name,
                detail=f"fields={'description' if req.description is not None else ''}"
                f"{',metadata' if req.metadata is not None else ''}",
            )
        else:
            if not state.tokens.update_profile(
                name, description=req.description, metadata=req.metadata
            ):
                raise HTTPException(404, f"token '{name}' not found")
            state.audit.record(
                action=AuditAction.token_update_profile,
                actor=actor.name,
                target=name,
                detail=f"fields={'description' if req.description is not None else ''}"
                f"{',metadata' if req.metadata is not None else ''}",
            )
        return {"message": f"profile for '{name}' updated"}

    @app.get(
        "/v1/users",
        response_model=UserListResponse,
        tags=["users"],
    )
    async def list_users(
        name: str = Query("", description="Filter by username (substring match)"),
        email: str = Query("", description="Filter by email (substring match)"),
        role: str = Query("", description="Filter by role (reader/publisher/admin)"),
        org: str = Query("", description="Filter by organization membership"),
        has_published: bool | None = Query(
            None,
            description="Filter by whether user has published packages",
        ),
        sort: str = Query("name", description="Sort field: name, email, or packages_published"),
        order: str = Query("asc", description="Sort order: asc or desc"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        """List user profiles with pagination and optional filters.

        Supports filtering by email (substring match), role, organization
        membership, and whether the user has published packages.
        Sorting by ``name``, ``email``, or ``packages_published``.
        Does not require authentication.
        """
        if sort not in ("name", "email", "packages_published"):
            raise HTTPException(422, f"invalid sort field: {sort}")
        if order not in ("asc", "desc"):
            raise HTTPException(422, f"invalid sort order: {order}")
        if _use_db:
            users, total = await _db_tokens.search_users(
                name=name,
                email=email,
                role=role,
                org=org,
                has_published=has_published,
                sort_by=sort,
                sort_order=order,
                limit=limit,
                offset=offset,
            )
            return UserListResponse(users=users, total=total)
        # YAML backend
        state = _get_state()
        # Get all matching users (no pagination at store level)
        records, _total = state.tokens.search_users(
            name=name,
            email=email,
            role=role,
            limit=10000,
            offset=0,
        )
        bundles = state.index.get("bundles", [])
        users = []
        for r in records:
            pkg_count = sum(1 for b in bundles if b.get("published_by") == r.name)
            if has_published is True and pkg_count == 0:
                continue
            if has_published is False and pkg_count > 0:
                continue
            users.append(
                UserProfileResponse(
                    name=r.name,
                    role=r.role.value,
                    email=r.email,
                    description=r.description,
                    metadata=r.metadata,
                    packages_published=pkg_count,
                    created_at=r.created_at,
                )
            )
        # Apply sorting for YAML backend
        if sort == "packages_published":
            users.sort(key=lambda u: u.packages_published, reverse=(order == "desc"))
        elif sort == "email":
            users.sort(key=lambda u: u.email, reverse=(order == "desc"))
        else:
            users.sort(key=lambda u: u.name, reverse=(order == "desc"))
        actual_total = len(users)
        page = users[offset : offset + limit]
        return UserListResponse(users=page, total=actual_total)

    @app.get(
        "/v1/users/by-email/{email:path}",
        response_model=UserProfileResponse,
        tags=["users"],
    )
    async def get_user_by_email(email: str):
        """Look up a user's public profile by email address.

        Returns the first active user matching the given email.
        Does not require authentication.
        """
        if _use_db:
            profile = await _db_tokens.get_profile_by_email(email)
        else:
            state = _get_state()
            record = state.tokens.get_profile_by_email(email)
            if record is not None:
                bundles = state.index.get("bundles", [])
                pkg_count = sum(1 for b in bundles if b.get("published_by") == record.name)
                profile = UserProfileResponse(
                    name=record.name,
                    role=record.role.value,
                    email=record.email,
                    description=record.description,
                    metadata=record.metadata,
                    packages_published=pkg_count,
                    created_at=record.created_at,
                )
            else:
                profile = None
        if profile is None:
            raise HTTPException(404, f"no user with email '{email}' found")
        return profile

    @app.get(
        "/v1/users/{name}",
        response_model=UserProfileResponse,
        tags=["users"],
    )
    async def get_user_profile(name: str):
        """Look up a user's public profile by name.

        Returns their name, role, email, description, metadata, and
        account creation date.  Does not require authentication.
        """
        if _use_db:
            profile = await _db_tokens.get_public_profile(name)
        else:
            state = _get_state()
            record = state.tokens.get_public_profile(name)
            if record is not None:
                bundles = state.index.get("bundles", [])
                pkg_count = sum(1 for b in bundles if b.get("published_by") == record.name)
                profile = UserProfileResponse(
                    name=record.name,
                    role=record.role.value,
                    email=record.email,
                    description=record.description,
                    metadata=record.metadata,
                    packages_published=pkg_count,
                    created_at=record.created_at,
                )
            else:
                profile = None
        if profile is None:
            raise HTTPException(404, f"user '{name}' not found")
        return profile

    # ── Registration (public) ──────────────────────────────

    @app.post("/v1/register", response_model=RegistrationResponse, tags=["registration"])
    async def register(req: RegistrationRequest):
        """Self-service token registration.

        In ``open`` mode the token is created immediately and returned.
        In ``admin-gated`` mode a pending request is recorded for admin
        approval.
        """
        if not req.name or not req.name.strip():
            raise HTTPException(422, "name is required")
        if not _is_valid_identifier(req.name):
            raise HTTPException(
                422,
                f"invalid username '{req.name}': must be a valid C identifier "
                "(start with a letter or underscore, followed by letters, digits, or underscores)",
            )
        if not req.email or not req.email.strip():
            raise HTTPException(422, "email is required for registration")

        if RegistrationMode.open == REGISTRATION_MODE:
            state = _get_state()
            try:
                if _use_db:
                    raw = await _db_tokens.create(
                        name=req.name,
                        role=req.role,
                        email=req.email,
                        description=req.description,
                        metadata=req.metadata,
                    )
                else:
                    raw = state.tokens.create(
                        name=req.name,
                        role=req.role,
                        email=req.email,
                        description=req.description,
                        metadata=req.metadata,
                    )
            except ValueError:
                raise HTTPException(
                    409,
                    f"username '{req.name}' is already taken",
                ) from None
            if _use_db:
                await _db_audit.record(
                    action=AuditAction.registration_request,
                    actor=req.name,
                    target=req.name,
                    detail=f"role={req.role.value} email={req.email} mode=open auto_approved",
                )
            else:
                state.audit.record(
                    action=AuditAction.registration_request,
                    actor=req.name,
                    target=req.name,
                    detail=f"role={req.role.value} email={req.email} mode=open auto_approved",
                )
            return RegistrationResponse(
                message="Token created. Save it — it will not be shown again.",
                token=raw,
            )
        else:
            # admin-gated: create a pending request
            if not _use_db:
                raise HTTPException(
                    501,
                    "admin-gated registration requires a database backend",
                )
            request_record = await _db_token_requests.create(
                name=req.name, email=req.email, role=req.role
            )
            await _db_audit.record(
                action=AuditAction.registration_request,
                actor=req.name,
                target=req.name,
                detail=(
                    f"role={req.role.value} email={req.email}"
                    f" mode=admin-gated request_id={request_record.id}"
                ),
            )
            return RegistrationResponse(
                message="Registration request submitted. An admin will review it.",
                request_id=request_record.id,
            )

    # ── Token request management (admin) ────────────────────

    @app.get(
        "/v1/token-requests",
        response_model=TokenRequestListResponse,
        tags=["registration"],
    )
    async def list_token_requests(
        status: TokenRequestStatus | None = Query(None),
        _actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        if not _use_db:
            raise HTTPException(501, "token requests require a database backend")
        requests = await _db_token_requests.list_requests(status=status)
        return TokenRequestListResponse(requests=requests, total=len(requests))

    @app.post("/v1/token-requests/{request_id}/approve", tags=["registration"])
    async def approve_token_request(
        request_id: int,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        if not _use_db:
            raise HTTPException(501, "token requests require a database backend")
        tr = await _db_token_requests.get(request_id)
        if tr is None:
            raise HTTPException(404, f"token request {request_id} not found")
        if tr.status != TokenRequestStatus.pending:
            raise HTTPException(409, f"token request {request_id} already {tr.status.value}")
        if not await _db_token_requests.resolve(
            request_id, TokenRequestStatus.approved, actor.name
        ):
            raise HTTPException(409, "request already resolved")
        raw = await _db_tokens.create(name=tr.name, role=tr.role, email=tr.email)
        await _db_audit.record(
            action=AuditAction.registration_approve,
            actor=actor.name,
            target=tr.name,
            detail=f"request_id={request_id} role={tr.role.value}",
        )
        return {
            "message": f"approved request {request_id} — token created for '{tr.name}'",
            "token": raw,
        }

    @app.post("/v1/token-requests/{request_id}/deny", tags=["registration"])
    async def deny_token_request(
        request_id: int,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        if not _use_db:
            raise HTTPException(501, "token requests require a database backend")
        tr = await _db_token_requests.get(request_id)
        if tr is None:
            raise HTTPException(404, f"token request {request_id} not found")
        if tr.status != TokenRequestStatus.pending:
            raise HTTPException(409, f"token request {request_id} already {tr.status.value}")
        if not await _db_token_requests.resolve(request_id, TokenRequestStatus.denied, actor.name):
            raise HTTPException(409, "request already resolved")
        await _db_audit.record(
            action=AuditAction.registration_deny,
            actor=actor.name,
            target=tr.name,
            detail=f"request_id={request_id}",
        )
        return {"message": f"denied token request {request_id}"}

    # ── Audit trail (admin) ─────────────────────────────────

    @app.get("/v1/audit", response_model=AuditLogResponse, tags=["audit"])
    async def get_audit_log(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        action: AuditAction | None = Query(None),
        target: str = Query(""),
        _actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        if _use_db:
            entries, total = await _db_audit.entries(
                limit=limit, offset=offset, action=action, target=target
            )
        else:
            state = _get_state()
            entries, total = state.audit.entries(
                limit=limit, offset=offset, action=action, target=target
            )
        return AuditLogResponse(entries=entries, total=total)

    @app.get("/v1/audit/verify", tags=["audit"])
    async def verify_audit_chain(
        _actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        if _use_db:
            ok, message = await _db_audit.verify_chain()
        else:
            state = _get_state()
            ok, message = state.audit.verify_chain()
        return {"ok": ok, "message": message}

    # ── Organizations ───────────────────────────────────────

    @app.post("/v1/orgs", response_model=OrgInfo, tags=["organizations"])
    async def create_org(
        body: OrgCreateRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        if not _use_db or _db_orgs is None:
            raise HTTPException(501, "organizations require database backend")

        # Pydantic regex validates char set; also reject consecutive hyphens.
        from cvcpkg.server.models import validate_org_slug

        err = validate_org_slug(body.slug)
        if err:
            raise HTTPException(422, err)

        try:
            org = await _db_orgs.create(
                slug=body.slug,
                display_name=body.display_name,
                description=body.description,
                logo_url=body.logo_url,
                homepage=body.homepage,
                is_private=body.is_private,
                created_by=actor.name,
                storage_limit_bytes=ORG_STORAGE_LIMIT_BYTES,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        await _db_audit.record(
            action=AuditAction.org_create,
            actor=actor.name,
            target=body.slug,
            detail=f"display_name={body.display_name}",
        )
        return org

    @app.get("/v1/orgs", response_model=OrgListResponse, tags=["organizations"])
    async def list_orgs(
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
    ):
        if not _use_db or _db_orgs is None:
            return OrgListResponse(total=0, organizations=[])
        orgs, total = await _db_orgs.list_orgs(
            limit=limit,
            offset=offset,
            include_private=False,
        )
        return OrgListResponse(total=total, organizations=orgs)

    @app.get("/v1/orgs/{slug}", response_model=OrgDetailResponse, tags=["organizations"])
    async def get_org(
        slug: str,
        caller: TokenRecord | None = Depends(optional_token),
    ):
        if not _use_db or _db_orgs is None:
            raise HTTPException(404, "organization not found")
        org = await _db_orgs.get(slug)
        if org is None:
            raise HTTPException(404, f"organization '{slug}' not found")
        # Determine caller's access level
        caller_name = caller.name if caller else None
        is_admin = caller is not None and caller.role == TokenRole.admin
        is_member = caller_name is not None and await _db_orgs.is_member(slug, caller_name)
        # Private orgs are only visible to members and admins
        if org.is_private and not is_admin and not is_member:
            raise HTTPException(404, f"organization '{slug}' not found")
        # Only members and admins can see the member list
        if is_admin or is_member:
            members = await _db_orgs.get_members(slug)
        else:
            members = []
        packages, _ = await _db_packages.get_bundles(org_slug=slug)
        return OrgDetailResponse(org=org, members=members, packages=packages)

    @app.patch("/v1/orgs/{slug}", response_model=OrgInfo, tags=["organizations"])
    async def update_org(
        slug: str,
        body: OrgUpdateRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        if not _use_db or _db_orgs is None:
            raise HTTPException(501, "organizations require database backend")
        if not await _db_orgs.is_owner(slug, actor.name) and actor.role != TokenRole.admin:
            raise HTTPException(403, "only org owners or admins can update organization settings")
        # storage_limit_bytes is admin-only
        if body.storage_limit_bytes is not None and actor.role != TokenRole.admin:
            raise HTTPException(403, "only admins can change storage limits")
        updated = await _db_orgs.update(
            slug,
            display_name=body.display_name,
            description=body.description,
            logo_url=body.logo_url,
            homepage=body.homepage,
            is_private=body.is_private,
            storage_limit_bytes=body.storage_limit_bytes,
        )
        if updated is None:
            raise HTTPException(404, f"organization '{slug}' not found")
        await _db_audit.record(
            action=AuditAction.org_update,
            actor=actor.name,
            target=slug,
        )
        return updated

    @app.post("/v1/orgs/{slug}/logo", tags=["organizations"])
    async def upload_org_logo(
        slug: str,
        file: UploadFile,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Upload an organization logo image (PNG, JPEG, SVG, WebP; max 512 KB)."""
        if not _use_db or _db_orgs is None:
            raise HTTPException(501, "organizations require database backend")
        if not await _db_orgs.is_owner(slug, actor.name) and actor.role != TokenRole.admin:
            raise HTTPException(403, "only org owners or admins can update the logo")
        org = await _db_orgs.get(slug)
        if org is None:
            raise HTTPException(404, f"organization '{slug}' not found")

        # Validate content type
        allowed = {"image/png", "image/jpeg", "image/svg+xml", "image/webp"}
        ct = (file.content_type or "").lower()
        if ct not in allowed:
            raise HTTPException(
                400, f"unsupported image type '{ct}'; allowed: {', '.join(sorted(allowed))}"
            )
        ext_map = {
            "image/png": ".png",
            "image/jpeg": ".jpg",
            "image/svg+xml": ".svg",
            "image/webp": ".webp",
        }
        ext = ext_map.get(ct, ".png")

        # Read and validate size (512 KB max)
        data = await file.read()
        if len(data) > 512 * 1024:
            raise HTTPException(400, "logo must be 512 KB or smaller")

        # Save to state_dir/logos/<slug>.<ext>
        logos_dir = _get_state().state_dir / "logos"
        logos_dir.mkdir(parents=True, exist_ok=True)
        # Remove any existing logo for this org
        for old in logos_dir.glob(f"{slug}.*"):
            old.unlink(missing_ok=True)
        logo_path = logos_dir / f"{slug}{ext}"
        logo_path.write_bytes(data)

        # Update the org's logo_url to the serve endpoint
        logo_url = f"/v1/orgs/{slug}/logo"
        await _db_orgs.update(slug, logo_url=logo_url)
        await _db_audit.record(
            action=AuditAction.org_update,
            actor=actor.name,
            target=slug,
            detail="logo uploaded",
        )
        return {"message": "logo uploaded", "logo_url": logo_url}

    @app.get("/v1/orgs/{slug}/logo", tags=["organizations"])
    async def serve_org_logo(slug: str):
        """Serve the uploaded logo for an organization."""
        logos_dir = _get_state().state_dir / "logos"
        for candidate in logos_dir.glob(f"{slug}.*"):
            if candidate.is_file():
                ct_map = {
                    ".png": "image/png",
                    ".jpg": "image/jpeg",
                    ".svg": "image/svg+xml",
                    ".webp": "image/webp",
                }
                content_type = ct_map.get(candidate.suffix.lower(), "application/octet-stream")
                return FileResponse(candidate, media_type=content_type)
        raise HTTPException(404, "no logo found")

    @app.post("/v1/orgs/{slug}/members", tags=["organizations"])
    async def add_org_member(
        slug: str,
        token_name: str = Query(..., description="Token name to add as member"),
        role: OrgRole = Query(OrgRole.member),
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        if not _use_db or _db_orgs is None:
            raise HTTPException(501, "organizations require database backend")
        if not await _db_orgs.is_owner(slug, actor.name) and actor.role != TokenRole.admin:
            raise HTTPException(403, "only org owners or admins can manage members")
        try:
            added = await _db_orgs.add_member(slug, token_name, role)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        if not added:
            raise HTTPException(409, f"'{token_name}' is already a member of '{slug}'")
        await _db_audit.record(
            action=AuditAction.org_add_member,
            actor=actor.name,
            target=slug,
            detail=f"member={token_name} role={role.value}",
        )
        return {"message": f"added '{token_name}' to '{slug}' as {role.value}"}

    @app.delete("/v1/orgs/{slug}/members/{token_name}", tags=["organizations"])
    async def remove_org_member(
        slug: str,
        token_name: str,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        if not _use_db or _db_orgs is None:
            raise HTTPException(501, "organizations require database backend")
        if not await _db_orgs.is_owner(slug, actor.name) and actor.role != TokenRole.admin:
            raise HTTPException(403, "only org owners or admins can manage members")
        try:
            removed = await _db_orgs.remove_member(slug, token_name)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        if not removed:
            raise HTTPException(404, f"'{token_name}' is not a member of '{slug}'")
        await _db_audit.record(
            action=AuditAction.org_remove_member,
            actor=actor.name,
            target=slug,
            detail=f"member={token_name}",
        )
        return {"message": f"removed '{token_name}' from '{slug}'"}

    # ── RSS Feed ────────────────────────────────────────────

    @app.get("/v1/feed.xml", tags=["feed"], response_class=Response)
    async def rss_feed(
        limit: int = Query(50, ge=1, le=200, description="Number of items in the feed"),
        _auth: None = Depends(optional_reader_auth),
    ):
        """RSS 2.0 feed of the latest published packages."""
        import xml.etree.ElementTree as ET

        if _use_db:
            packages, _ = await _db_packages.get_bundles(limit=limit, offset=0)
        else:
            state = _get_state()
            bundles = state.index.get("bundles", [])
            bundles = [b for b in bundles if not b.get("yanked", False)]
            bundles = sorted(
                bundles,
                key=lambda b: b.get("published_at", ""),
                reverse=True,
            )[:limit]
            packages = [
                PackageInfo(
                    name=b["name"],
                    version=b["version"],
                    platform=b.get("platform", ""),
                    arch=b.get("arch", ""),
                    build_type=b.get("build_type", ""),
                    link=b.get("link", ""),
                    sha256=b.get("sha256", ""),
                    size_bytes=b.get("size_bytes", 0),
                    archive_url=b.get("archive_url", ""),
                    published_at=b.get("published_at", "1970-01-01T00:00:00+00:00"),
                    description=b.get("description", ""),
                    homepage=b.get("homepage", ""),
                    license=b.get("license", ""),
                    maintainer=b.get("maintainer", ""),
                )
                for b in bundles
            ]

        # Sort by published_at descending for the feed
        packages.sort(key=lambda p: p.published_at, reverse=True)

        rss = ET.Element("rss", version="2.0")
        channel = ET.SubElement(rss, "channel")
        site_title = os.environ.get("CVCPKG_SITE_TITLE", "cvcpkg")
        github_url = (
            f"https://github.com/{os.environ.get('CVCPKG_GITHUB_REPO', 'transfix/libcvc-deps')}"
        )
        ET.SubElement(channel, "title").text = f"{site_title} — Latest Packages"
        ET.SubElement(channel, "link").text = github_url
        ET.SubElement(channel, "description").text = f"Latest packages published to {site_title}"
        ET.SubElement(channel, "language").text = "en"
        if packages:
            ET.SubElement(channel, "lastBuildDate").text = packages[0].published_at.strftime(
                "%a, %d %b %Y %H:%M:%S +0000"
            )

        for pkg in packages:
            item = ET.SubElement(channel, "item")
            title_text = f"{pkg.name} {pkg.version}"
            if pkg.platform:
                title_text += f" ({pkg.platform})"
            ET.SubElement(item, "title").text = title_text
            ET.SubElement(item, "link").text = github_url
            desc_parts = [f"Package: {pkg.name} v{pkg.version}"]
            if pkg.platform:
                desc_parts.append(f"Platform: {pkg.platform}/{pkg.arch}")
            if pkg.description:
                desc_parts.append(pkg.description)
            if pkg.size_bytes:
                size_mb = pkg.size_bytes / (1024 * 1024)
                desc_parts.append(f"Size: {size_mb:.1f} MB")
            ET.SubElement(item, "description").text = " — ".join(desc_parts)
            ET.SubElement(item, "pubDate").text = pkg.published_at.strftime(
                "%a, %d %b %Y %H:%M:%S +0000"
            )
            ET.SubElement(item, "guid", isPermaLink="false").text = (
                f"{pkg.name}-{pkg.version}-{pkg.platform}-{pkg.arch}-{pkg.build_type}-{pkg.link}"
            )

        xml_bytes = ET.tostring(rss, encoding="unicode", xml_declaration=False)
        xml_out = '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_bytes
        return Response(content=xml_out, media_type="application/rss+xml; charset=utf-8")

    # ── Download stats ──────────────────────────────────────

    @app.get("/v1/downloads/stats", tags=["analytics"])
    async def download_stats(
        name: str = Query("", description="Filter by package name (empty = all packages)"),
        days: int = Query(
            DOWNLOAD_GRAPH_DAYS,
            ge=1,
            le=365,
            description="Number of days of history",
        ),
        _auth: None = Depends(optional_reader_auth),
    ):
        """Get daily download counts for charting."""
        if not _use_db or _db_downloads is None:
            return JSONResponse(
                {
                    "total": 0,
                    "daily": [],
                    "config": {
                        "days": days,
                        "color": DOWNLOAD_GRAPH_COLOR,
                        "fill_color": DOWNLOAD_GRAPH_FILL_COLOR,
                        "height": DOWNLOAD_GRAPH_HEIGHT,
                    },
                }
            )
        total = await _db_downloads.get_total_downloads(package_name=name)
        daily = await _db_downloads.get_daily_downloads(package_name=name, days=days)
        return JSONResponse(
            {
                "total": total,
                "daily": daily,
                "config": {
                    "days": days,
                    "color": DOWNLOAD_GRAPH_COLOR,
                    "fill_color": DOWNLOAD_GRAPH_FILL_COLOR,
                    "height": DOWNLOAD_GRAPH_HEIGHT,
                },
            }
        )

    # ── Admin settings ──────────────────────────────────────

    @app.get("/v1/admin/settings", tags=["admin"])
    async def get_admin_settings(
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Return current server-wide settings (admin-only)."""
        return {
            "global_cache_storage_limit_bytes": GLOBAL_CACHE_STORAGE_LIMIT_BYTES,
            "org_storage_limit_bytes": ORG_STORAGE_LIMIT_BYTES,
            "max_upload_bytes": MAX_UPLOAD_BYTES,
            "rate_limit_rpm": RATE_LIMIT_RPM,
        }

    @app.patch("/v1/admin/settings", tags=["admin"])
    async def update_admin_settings(
        request: Request,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Update server-wide settings at runtime (admin-only).

        Accepts a JSON body with one or more of:
        ``global_cache_storage_limit_bytes``, ``org_storage_limit_bytes``.
        Changes take effect immediately but are **not** persisted across
        server restarts — use environment variables for permanent config.
        """
        global GLOBAL_CACHE_STORAGE_LIMIT_BYTES, ORG_STORAGE_LIMIT_BYTES  # noqa: PLW0603

        body = await request.json()
        changed: dict[str, int] = {}
        if "global_cache_storage_limit_bytes" in body:
            val = int(body["global_cache_storage_limit_bytes"])
            if val < 0:
                raise HTTPException(422, "global_cache_storage_limit_bytes must be >= 0")
            GLOBAL_CACHE_STORAGE_LIMIT_BYTES = val
            changed["global_cache_storage_limit_bytes"] = val
        if "org_storage_limit_bytes" in body:
            val = int(body["org_storage_limit_bytes"])
            if val < 0:
                raise HTTPException(422, "org_storage_limit_bytes must be >= 0")
            ORG_STORAGE_LIMIT_BYTES = val
            changed["org_storage_limit_bytes"] = val
        if not changed:
            raise HTTPException(422, "no recognised settings in request body")
        await _db_audit.record(
            action=AuditAction.admin_settings_update,
            actor=actor.name,
            target="settings",
            detail=str(changed),
        )
        return {"message": "settings updated", "updated": changed}

    # ── Org HTML pages ──────────────────────────────────────

    @app.get("/orgs", response_class=HTMLResponse, include_in_schema=False)
    async def orgs_listing_page():
        from cvcpkg.server.landing import orgs_listing_html

        return HTMLResponse(orgs_listing_html())

    @app.get("/org/{slug}", response_class=HTMLResponse, include_in_schema=False)
    async def org_detail_page(slug: str):
        from cvcpkg.server.landing import org_detail_html

        return HTMLResponse(org_detail_html(slug))

    # ── Tag HTML pages ──────────────────────────────────────

    @app.get("/tags", response_class=HTMLResponse, include_in_schema=False)
    async def tags_listing_page():
        from cvcpkg.server.landing import tags_listing_html

        return HTMLResponse(tags_listing_html())

    @app.get("/tag/{tag_name}", response_class=HTMLResponse, include_in_schema=False)
    async def tag_detail_page(tag_name: str, org: str = ""):
        from cvcpkg.server.landing import tag_detail_html

        return HTMLResponse(tag_detail_html(tag_name, org))

    # ── Tag API endpoints ───────────────────────────────────

    @app.get("/v1/tags", response_model=TagListResponse, tags=["tags"])
    async def list_tags(
        org: str = "",
        limit: int = 200,
        offset: int = 0,
        _auth: TokenRecord | None = Depends(optional_reader_auth),
    ):
        """List curated tags with package counts."""
        if not _use_db or _db_tags is None:
            return TagListResponse(total=0, tags=[])
        org_filter: str | None = org if org else None
        # If the org is private, check access
        if org and _db_orgs is not None:
            org_info = await _db_orgs.get(org)
            if org_info and org_info.is_private:
                caller = _auth
                if caller is None or (
                    caller.role != TokenRole.admin
                    and not await _db_orgs.is_member(org, caller.name)
                ):
                    raise HTTPException(403, "private organization -- access denied")
        tags, total = await _db_tags.list_tags(org_slug=org_filter, limit=limit, offset=offset)
        return TagListResponse(total=total, tags=tags)

    @app.get("/v1/tags/all", tags=["tags"])
    async def list_all_tags(
        _auth: TokenRecord | None = Depends(optional_reader_auth),
    ):
        """Return all tag names (curated + ad-hoc) with package counts.

        Used by the front-page tag browser.
        """
        if not _use_db or _db_tags is None:
            return {"tags": []}
        all_tags = await _db_tags.list_all_tag_names()
        # Filter out tags belonging to private orgs for unauthenticated users
        if _db_orgs is not None:
            visible: list[dict] = []
            for t in all_tags:
                org_slug = t.get("org_slug", "")
                if org_slug:
                    org_info = await _db_orgs.get(org_slug)
                    if org_info and org_info.is_private:
                        if _auth is None or (
                            _auth.role != TokenRole.admin
                            and not await _db_orgs.is_member(org_slug, _auth.name)
                        ):
                            continue
                visible.append(t)
            return {"tags": visible}
        return {"tags": all_tags}

    @app.post("/v1/tags", response_model=TagInfo, tags=["tags"])
    async def create_tag(
        body: TagCreateRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Create a curated tag (admin or org-owner only)."""
        if not _use_db or _db_tags is None:
            raise HTTPException(501, "tags require a database backend")
        # For org-scoped tags, verify the actor is org owner or global admin
        if body.org_slug:
            if actor.role != TokenRole.admin:
                if _db_orgs is None:
                    raise HTTPException(501, "org support requires a database backend")
                if not await _db_orgs.is_owner(body.org_slug, actor.name):
                    raise HTTPException(
                        403,
                        "only org owners or global admins can create org-scoped tags",
                    )
        try:
            tag = await _db_tags.create(
                name=body.name,
                org_slug=body.org_slug,
                display_name=body.display_name,
                description=body.description,
                logo_url=body.logo_url,
                created_by=actor.name,
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        await _db_audit.record(
            action=AuditAction.tag_create,
            actor=actor.name,
            target=tag.qualified_name,
            detail=f"display_name={tag.display_name}",
        )
        return tag

    @app.put("/v1/tags/{tag_name}", response_model=TagInfo, tags=["tags"])
    async def update_tag(
        tag_name: str,
        body: TagUpdateRequest,
        org: str = "",
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Update a curated tag (admin or org-owner only)."""
        if not _use_db or _db_tags is None:
            raise HTTPException(501, "tags require a database backend")
        if org:
            if actor.role != TokenRole.admin:
                if _db_orgs is None:
                    raise HTTPException(501, "org support requires a database backend")
                if not await _db_orgs.is_owner(org, actor.name):
                    raise HTTPException(
                        403,
                        "only org owners or global admins can edit org-scoped tags",
                    )
        tag = await _db_tags.update(
            name=tag_name,
            org_slug=org,
            display_name=body.display_name,
            description=body.description,
            logo_url=body.logo_url,
        )
        if tag is None:
            raise HTTPException(404, "tag not found")
        await _db_audit.record(
            action=AuditAction.tag_update,
            actor=actor.name,
            target=tag.qualified_name,
        )
        return tag

    @app.delete("/v1/tags/{tag_name}", tags=["tags"])
    async def delete_tag(
        tag_name: str,
        org: str = "",
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Delete a curated tag (admin or org-owner only)."""
        if not _use_db or _db_tags is None:
            raise HTTPException(501, "tags require a database backend")
        if org:
            if actor.role != TokenRole.admin:
                if _db_orgs is None:
                    raise HTTPException(501, "org support requires a database backend")
                if not await _db_orgs.is_owner(org, actor.name):
                    raise HTTPException(
                        403,
                        "only org owners or global admins can delete org-scoped tags",
                    )
        deleted = await _db_tags.delete(name=tag_name, org_slug=org)
        if not deleted:
            raise HTTPException(404, "tag not found")
        qualified = f"{org}/{tag_name}" if org else tag_name
        await _db_audit.record(
            action=AuditAction.tag_delete,
            actor=actor.name,
            target=qualified,
        )
        return {"message": f"tag '{qualified}' deleted"}

    # ── Mirror endpoints ────────────────────────────────────

    @app.post("/v1/mirrors/register", response_model=MirrorInfo, tags=["mirrors"])
    async def register_mirror(body: MirrorRegisterRequest):
        """Register a mirror with the primary server.

        Mirrors call this endpoint to announce themselves.  The primary
        will periodically health-check registered mirrors and include
        healthy ones in the mirror list served to clients.

        Re-registering an existing URL clears any previous rejection
        and resets health state.
        """
        if MIRROR_MODE:
            raise HTTPException(403, "mirror servers cannot register other mirrors")
        if not _use_db or _db_mirrors is None:
            raise HTTPException(
                501,
                "mirror registry requires a database backend (set CVCPKG_DATABASE_URL)",
            )
        url = body.url.rstrip("/")
        if not url.startswith(("http://", "https://")):
            raise HTTPException(422, "mirror URL must start with http:// or https://")
        info = await _db_mirrors.register(
            url=url,
            display_name=body.display_name,
            contact=body.contact,
        )
        await _db_audit.record(
            action=AuditAction.mirror_register,
            actor="mirror",
            target=url,
            detail=body.display_name or "",
        )
        return info

    @app.get("/v1/mirrors", response_model=MirrorListResponse, tags=["mirrors"])
    async def list_mirrors(
        _auth: TokenRecord | None = Depends(optional_reader_auth),
    ):
        """List healthy mirrors that clients can use for failover.

        This endpoint is public (or reader-auth if configured) so
        clients can cache the mirror list for use when the primary
        is unreachable.
        """
        if not _use_db or _db_mirrors is None:
            return MirrorListResponse(total=0, mirrors=[])
        mirrors = await _db_mirrors.list_healthy()
        return MirrorListResponse(total=len(mirrors), mirrors=mirrors)

    @app.get("/v1/mirrors/all", response_model=MirrorListResponse, tags=["mirrors"])
    async def list_all_mirrors(
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """List all mirrors including rejected and unhealthy (admin-only)."""
        if not _use_db or _db_mirrors is None:
            return MirrorListResponse(total=0, mirrors=[])
        mirrors = await _db_mirrors.list_all()
        return MirrorListResponse(total=len(mirrors), mirrors=mirrors)

    @app.post("/v1/mirrors/reject", tags=["mirrors"])
    async def reject_mirror(
        url: str = Query(..., description="URL of the mirror to reject"),
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Reject a mirror, removing it from the public mirror list.

        Rejected mirrors are retained in the database for audit purposes
        but are excluded from the healthy mirror list.  A mirror can
        re-register to clear its rejection.
        """
        if not _use_db or _db_mirrors is None:
            raise HTTPException(501, "mirror registry requires a database backend")
        found = await _db_mirrors.reject(url, actor.name)
        if not found:
            raise HTTPException(404, f"mirror not found: {url}")
        await _db_audit.record(
            action=AuditAction.mirror_reject,
            actor=actor.name,
            target=url,
        )
        return {"message": "mirror rejected", "url": url}

    @app.delete("/v1/mirrors", tags=["mirrors"])
    async def remove_mirror(
        url: str = Query(..., description="URL of the mirror to remove"),
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Permanently remove a mirror from the registry (admin-only)."""
        if not _use_db or _db_mirrors is None:
            raise HTTPException(501, "mirror registry requires a database backend")
        found = await _db_mirrors.remove(url)
        if not found:
            raise HTTPException(404, f"mirror not found: {url}")
        await _db_audit.record(
            action=AuditAction.mirror_remove,
            actor=actor.name,
            target=url,
        )
        return {"message": "mirror removed", "url": url}

    # ── Builders ────────────────────────────────────────────

    def _require_db_builders():
        if not _use_db or _db_builders is None:
            raise HTTPException(
                501, "builder registry requires a database backend (set CVCPKG_DATABASE_URL)"
            )

    @app.post("/v1/builders/register", response_model=BuilderInfo, tags=["builders"])
    async def register_builder(
        body: BuilderRegisterRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Register a new builder or re-register an existing one."""
        _require_db_builders()
        info = await _db_builders.register(
            name=body.name,
            platform=body.platform,
            arch=body.arch,
            registered_by=actor.name,
            org_slug=body.org_slug,
            labels=body.labels,
            capabilities=body.capabilities,
            max_jobs=body.max_jobs,
            prefer_affinity=body.prefer_affinity,
        )
        await _db_audit.record(
            action=AuditAction.builder_register,
            actor=actor.name,
            target=f"{body.org_slug}/{body.name}" if body.org_slug else body.name,
            detail=f"{body.platform}/{body.arch}",
        )
        await emit_webhook_event(
            "builder.online",
            {
                "builder_name": body.name,
                "platform": body.platform,
                "arch": body.arch,
                "builder_id": info.id,
            },
            org_slug=body.org_slug,
        )
        return info

    @app.get("/v1/builders", response_model=BuilderListResponse, tags=["builders"])
    async def list_builders(
        org_slug: str | None = Query(None, description="Filter by org slug"),
        platform: str | None = Query(None, description="Filter by platform"),
        arch: str | None = Query(None, description="Filter by architecture"),
        status: str | None = Query(None, description="Filter by status"),
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """List registered builders."""
        _require_db_builders()
        builders = await _db_builders.list_builders(
            org_slug=org_slug, platform=platform, arch=arch, status=status
        )
        return BuilderListResponse(total=len(builders), builders=builders)

    @app.get("/v1/builders/{builder_id}", response_model=BuilderInfo, tags=["builders"])
    async def get_builder(
        builder_id: int,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Get a single builder by ID."""
        _require_db_builders()
        info = await _db_builders.get(builder_id)
        if info is None:
            raise HTTPException(404, f"builder {builder_id} not found")
        return info

    @app.patch("/v1/builders/{builder_id}", response_model=BuilderInfo, tags=["builders"])
    async def update_builder(
        builder_id: int,
        body: BuilderUpdateRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Update mutable builder fields."""
        _require_db_builders()
        info = await _db_builders.update(
            builder_id,
            labels=body.labels,
            capabilities=body.capabilities,
            max_jobs=body.max_jobs,
            prefer_affinity=body.prefer_affinity,
        )
        if info is None:
            raise HTTPException(404, f"builder {builder_id} not found")
        await _db_audit.record(
            action=AuditAction.builder_update,
            actor=actor.name,
            target=str(builder_id),
        )
        return info

    @app.post(
        "/v1/builders/{builder_id}/heartbeat",
        response_model=BuilderInfo,
        tags=["builders"],
    )
    async def builder_heartbeat(
        builder_id: int,
        body: BuilderHeartbeatRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Record a heartbeat from a running builder."""
        _require_db_builders()
        info = await _db_builders.heartbeat(
            builder_id, status=body.status, current_jobs=body.current_jobs
        )
        if info is None:
            raise HTTPException(404, f"builder {builder_id} not found")
        return info

    @app.delete("/v1/builders/{builder_id}", tags=["builders"])
    async def unregister_builder(
        builder_id: int,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Unregister (delete) a builder. Admin-only."""
        _require_db_builders()
        info = await _db_builders.get(builder_id)
        if info is None:
            raise HTTPException(404, f"builder {builder_id} not found")
        await _db_builders.unregister(builder_id)
        await _db_audit.record(
            action=AuditAction.builder_unregister,
            actor=actor.name,
            target=f"{info.org_slug}/{info.name}" if info.org_slug else info.name,
        )
        await emit_webhook_event(
            "builder.offline",
            {
                "builder_name": info.name,
                "builder_id": builder_id,
            },
            org_slug=info.org_slug,
        )
        return {"message": "builder unregistered", "id": builder_id}

    # ── Build Jobs ─────────────────────────────────────────────

    def _require_db_build_jobs():
        if not _use_db or _db_build_jobs is None:
            raise HTTPException(
                501, "build jobs require a database backend (set CVCPKG_DATABASE_URL)"
            )

    @app.post("/v1/builds", response_model=BuildJobInfo, tags=["builds"])
    async def submit_build(
        body: BuildJobSubmitRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Submit a single build job."""
        _require_db_build_jobs()
        info = await _db_build_jobs.create(
            recipe_name=body.recipe_name,
            platform=body.platform,
            arch=body.arch,
            submitted_by=actor.name,
            recipe_version=body.recipe_version,
            recipe_hash=body.recipe_hash,
            config=body.config,
            link=body.link,
            org_slug=body.org_slug,
            priority=body.priority,
            timeout_seconds=body.timeout_seconds,
            depends_on=body.depends_on,
        )
        await _db_audit.record(
            action=AuditAction.build_submit,
            actor=actor.name,
            target=f"{body.recipe_name}@{body.platform}/{body.arch}",
            detail=f"job #{info.id}",
        )
        return info

    @app.post("/v1/builds/dag", response_model=DagSubmitResponse, tags=["builds"])
    async def submit_dag(
        body: DagSubmitRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Submit a DAG of build jobs.

        The ``depends_on`` fields in each job use 0-based indices into
        the ``jobs`` array (resolved to real IDs after insertion).
        """
        import uuid as _uuid

        _require_db_build_jobs()
        dag_id = body.dag_id or str(_uuid.uuid4())[:12]
        jobs_dicts = [
            {
                "recipe_name": j.recipe_name,
                "recipe_version": j.recipe_version,
                "recipe_hash": j.recipe_hash,
                "platform": j.platform,
                "arch": j.arch,
                "config": j.config,
                "link": j.link,
                "org_slug": j.org_slug,
                "priority": j.priority,
                "timeout_seconds": j.timeout_seconds,
                "depends_on": j.depends_on,
            }
            for j in body.jobs
        ]
        infos = await _db_build_jobs.create_dag(
            jobs=jobs_dicts, dag_id=dag_id, submitted_by=actor.name
        )
        await _db_audit.record(
            action=AuditAction.build_submit,
            actor=actor.name,
            target=f"dag:{dag_id}",
            detail=f"{len(infos)} jobs",
        )
        return DagSubmitResponse(dag_id=dag_id, total=len(infos), jobs=infos)

    @app.get("/v1/builds", response_model=BuildJobListResponse, tags=["builds"])
    async def list_builds(
        status: str | None = Query(None, description="Filter by status"),
        platform: str | None = Query(None, description="Filter by platform"),
        dag_id: str | None = Query(None, description="Filter by DAG ID"),
        recipe_name: str | None = Query(None, description="Filter by recipe name"),
        org_slug: str | None = Query(None, description="Filter by org"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """List build jobs with optional filters."""
        _require_db_build_jobs()
        jobs, total = await _db_build_jobs.list_jobs(
            status=status,
            platform=platform,
            dag_id=dag_id,
            recipe_name=recipe_name,
            org_slug=org_slug,
            limit=limit,
            offset=offset,
        )
        return BuildJobListResponse(total=total, jobs=jobs)

    @app.get("/v1/builds/{job_id}", response_model=BuildJobInfo, tags=["builds"])
    async def get_build(
        job_id: int,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Get a single build job by ID."""
        _require_db_build_jobs()
        info = await _db_build_jobs.get(job_id)
        if info is None:
            raise HTTPException(404, f"build job {job_id} not found")
        return info

    @app.post("/v1/builds/{job_id}/cancel", tags=["builds"])
    async def cancel_build(
        job_id: int,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Cancel a pending or dispatched build job."""
        _require_db_build_jobs()
        info = await _db_build_jobs.cancel(job_id)
        if info is None:
            raise HTTPException(404, f"build job {job_id} not found")
        await _db_audit.record(
            action=AuditAction.build_cancel,
            actor=actor.name,
            target=str(job_id),
        )
        await emit_webhook_event(
            "build.cancelled",
            {
                "job_id": job_id,
                "recipe_name": info.recipe_name,
                "platform": info.platform,
                "cancelled_by": actor.name,
            },
            org_slug=info.org_slug,
        )
        return {"message": "job cancelled", "id": job_id, "status": info.status}

    @app.post("/v1/builds/dag/{dag_id}/cancel", tags=["builds"])
    async def cancel_dag(
        dag_id: str,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Cancel all pending/dispatched jobs in a DAG."""
        _require_db_build_jobs()
        count = await _db_build_jobs.cancel_dag(dag_id)
        await _db_audit.record(
            action=AuditAction.build_cancel,
            actor=actor.name,
            target=f"dag:{dag_id}",
            detail=f"{count} jobs cancelled",
        )
        await emit_webhook_event(
            "build.cancelled",
            {
                "dag_id": dag_id,
                "cancelled": count,
                "cancelled_by": actor.name,
            },
        )
        return {"message": "dag cancelled", "dag_id": dag_id, "cancelled": count}

    @app.post(
        "/v1/builds/{job_id}/claim",
        response_model=BuildJobInfo,
        tags=["builds"],
    )
    async def claim_build(
        job_id: int,
        body: BuildJobClaimRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Builder claims a dispatched job → running."""
        _require_db_build_jobs()
        info = await _db_build_jobs.claim(job_id, body.builder_id)
        if info is None:
            raise HTTPException(404, f"build job {job_id} not found")
        await _db_audit.record(
            action=AuditAction.build_claim,
            actor=actor.name,
            target=str(job_id),
            detail=f"builder #{body.builder_id}",
        )
        await emit_webhook_event(
            "build.started",
            {
                "job_id": job_id,
                "recipe_name": info.recipe_name,
                "platform": info.platform,
                "arch": info.arch,
                "builder_id": body.builder_id,
            },
            org_slug=info.org_slug,
        )
        return info

    @app.post(
        "/v1/builds/{job_id}/complete",
        response_model=BuildJobInfo,
        tags=["builds"],
    )
    async def complete_build(
        job_id: int,
        body: BuildJobCompleteRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Report a build job as completed successfully."""
        _require_db_build_jobs()
        info = await _db_build_jobs.complete(job_id, result_archive_url=body.result_archive_url)
        if info is None:
            raise HTTPException(404, f"build job {job_id} not found")
        await _db_audit.record(
            action=AuditAction.build_complete,
            actor=actor.name,
            target=str(job_id),
        )
        await emit_webhook_event(
            "build.completed",
            {
                "job_id": job_id,
                "recipe_name": info.recipe_name,
                "platform": info.platform,
                "arch": info.arch,
                "result_archive_url": info.result_archive_url or "",
            },
            org_slug=info.org_slug,
        )
        # Check if the entire DAG is now complete
        if info.dag_id:
            done = await _db_build_jobs.is_dag_complete(info.dag_id)
            if done:
                summary = await _db_build_jobs.dag_summary(info.dag_id)
                await emit_webhook_event(
                    "build.dag_completed",
                    summary,
                    org_slug=info.org_slug,
                )
        return info

    @app.post(
        "/v1/builds/{job_id}/fail",
        response_model=BuildJobInfo,
        tags=["builds"],
    )
    async def fail_build(
        job_id: int,
        body: BuildJobFailRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Report a build job as failed."""
        _require_db_build_jobs()
        info = await _db_build_jobs.fail(job_id, error_message=body.error_message)
        if info is None:
            raise HTTPException(404, f"build job {job_id} not found")
        await _db_audit.record(
            action=AuditAction.build_fail,
            actor=actor.name,
            target=str(job_id),
            detail=body.error_message[:200] if body.error_message else "",
        )
        await emit_webhook_event(
            "build.failed",
            {
                "job_id": job_id,
                "recipe_name": info.recipe_name,
                "platform": info.platform,
                "arch": info.arch,
                "error_message": body.error_message or "",
            },
            org_slug=info.org_slug,
        )
        # Check if the entire DAG is now complete
        if info.dag_id:
            done = await _db_build_jobs.is_dag_complete(info.dag_id)
            if done:
                summary = await _db_build_jobs.dag_summary(info.dag_id)
                await emit_webhook_event(
                    "build.dag_completed",
                    summary,
                    org_slug=info.org_slug,
                )
        return info

    # ── Build log endpoints ─────────────────────────────────

    @app.patch(
        "/v1/builds/{job_id}/log",
        response_model=BuildJobInfo,
        tags=["builds"],
    )
    async def append_build_log(
        job_id: int,
        body: BuildLogAppendRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Append a chunk of log data to a build job's log."""
        _require_db_build_jobs()
        state = _get_state()
        info = await _db_build_jobs.append_log(job_id, body.data, logs_dir=state.logs_dir())
        if info is None:
            raise HTTPException(404, f"build job {job_id} not found")
        return info

    @app.get(
        "/v1/builds/{job_id}/log",
        tags=["builds"],
    )
    async def download_build_log(
        job_id: int,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Download the full log for a build job."""
        _require_db_build_jobs()
        state = _get_state()
        path = await _db_build_jobs.get_log_path(job_id, logs_dir=state.logs_dir())
        if path is None:
            raise HTTPException(404, f"no log available for build job {job_id}")
        return FileResponse(
            path,
            media_type="text/plain",
            filename=path.name,
        )

    @app.delete(
        "/v1/builds/{job_id}/log",
        tags=["builds"],
    )
    async def delete_build_log(
        job_id: int,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Delete a build job's log (admin only)."""
        _require_db_build_jobs()
        state = _get_state()
        deleted = await _db_build_jobs.delete_log(job_id, logs_dir=state.logs_dir())
        if not deleted:
            raise HTTPException(404, f"build job {job_id} not found")
        return {"ok": True, "job_id": job_id}

    @app.get(
        "/v1/builds/{job_id}/log/stream",
        tags=["builds"],
    )
    async def stream_build_log(
        job_id: int,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Stream a build job's log as Server-Sent Events.

        Reads existing log content and then tails for new data.
        The stream ends when the job reaches a terminal state.
        """
        _require_db_build_jobs()
        state = _get_state()

        async def _event_generator():
            logs_dir = state.logs_dir()
            offset = 0
            while True:
                info = await _db_build_jobs.get(job_id)
                if info is None:
                    yield "event: error\ndata: job not found\n\n"
                    return

                path = await _db_build_jobs.get_log_path(job_id, logs_dir=logs_dir)
                if path is not None:
                    size = path.stat().st_size
                    if size > offset:
                        with open(path) as f:
                            f.seek(offset)
                            chunk = f.read()
                        offset = size
                        # Escape newlines for SSE
                        for line in chunk.splitlines():
                            yield f"data: {line}\n\n"

                # Check terminal states
                if info.status in (
                    "succeeded",
                    "failed",
                    "cancelled",
                    "timed_out",
                ):
                    yield f"event: done\ndata: {info.status}\n\n"
                    return
                await asyncio.sleep(2)

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
        )

    # ── Builder next-job (long-poll) ────────────────────────

    @app.get(
        "/v1/builders/{builder_id}/next-job",
        tags=["builders"],
    )
    async def builder_next_job(
        builder_id: int,
        timeout: int = Query(default=30, ge=1, le=60),
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Long-poll for the next dispatched job for a builder.

        Blocks for up to ``timeout`` seconds (default 30, max 60).
        Returns a job object or 204 No Content.
        """
        _require_db_build_jobs()
        deadline = time.time() + timeout
        while time.time() < deadline:
            info = await _db_build_jobs.next_job_for_builder(builder_id)
            if info is not None:
                return info
            await asyncio.sleep(1)
        return Response(status_code=204)

    # ── Builder WebSocket ───────────────────────────────────

    @app.websocket("/v1/builders/{builder_id}/ws")
    async def builder_ws(websocket: WebSocket, builder_id: int):
        """Persistent WebSocket for a builder.

        The builder authenticates by passing ``token`` as a query
        parameter.  Once connected the builder receives
        ``job.dispatch``, ``job.timeout``, ``recipe.push``, and
        ``ping`` messages from the server.  The builder sends
        ``job.claim``, ``job.log``, ``job.complete``, ``job.fail``,
        ``heartbeat``, and ``pong`` messages.
        """
        # Authenticate via query param
        token_value = websocket.query_params.get("token", "")
        if not token_value:
            await websocket.close(code=4001, reason="missing token")
            return
        actor = await _authenticate_token(token_value)
        if actor is None:
            await websocket.close(code=4001, reason="invalid token")
            return
        if actor.role not in (TokenRole.publisher, TokenRole.admin):
            await websocket.close(code=4003, reason="insufficient role")
            return

        # Validate builder exists
        if not _use_db or _db_builders is None:
            await websocket.close(code=4004, reason="builders not available")
            return
        info = await _db_builders.get(builder_id)
        if info is None:
            await websocket.close(code=4004, reason="builder not found")
            return

        await websocket.accept()
        _ws_builders[builder_id] = websocket
        logger.info("builder %d connected via WebSocket", builder_id)

        try:
            while True:
                data = await websocket.receive_json()
                msg_type = data.get("type", "")

                if msg_type == "heartbeat":
                    status = data.get("status", "online")
                    current_jobs = data.get("current_jobs", 0)
                    await _db_builders.heartbeat(
                        builder_id, status=status, current_jobs=current_jobs
                    )
                    await websocket.send_json({"type": "heartbeat_ack"})

                elif msg_type == "job.claim":
                    job_id = data.get("job_id")
                    if job_id is not None:
                        result = await _db_build_jobs.claim(job_id, builder_id)
                        await websocket.send_json(
                            {
                                "type": "job.claim_ack",
                                "job_id": job_id,
                                "status": result.status if result else "not_found",
                            }
                        )

                elif msg_type == "job.log":
                    job_id = data.get("job_id")
                    log_data = data.get("data", "")
                    if job_id is not None and log_data:
                        state = _get_state()
                        await _db_build_jobs.append_log(job_id, log_data, logs_dir=state.logs_dir())

                elif msg_type == "job.complete":
                    job_id = data.get("job_id")
                    archive_url = data.get("archive_url", "")
                    if job_id is not None:
                        result = await _db_build_jobs.complete(
                            job_id, result_archive_url=archive_url
                        )
                        await websocket.send_json(
                            {
                                "type": "job.complete_ack",
                                "job_id": job_id,
                                "status": result.status if result else "not_found",
                            }
                        )
                        if result:
                            await emit_webhook_event(
                                "build.succeeded",
                                {
                                    "job_id": job_id,
                                    "recipe_name": result.recipe_name,
                                    "platform": result.platform,
                                    "arch": result.arch,
                                    "archive_url": archive_url,
                                },
                                org_slug=result.org_slug,
                            )
                            if result.dag_id:
                                done = await _db_build_jobs.is_dag_complete(result.dag_id)
                                if done:
                                    summary = await _db_build_jobs.dag_summary(result.dag_id)
                                    await emit_webhook_event(
                                        "build.dag_completed",
                                        summary,
                                        org_slug=result.org_slug,
                                    )

                elif msg_type == "job.fail":
                    job_id = data.get("job_id")
                    error = data.get("error", "")
                    if job_id is not None:
                        result = await _db_build_jobs.fail(job_id, error_message=error[:4096])
                        await websocket.send_json(
                            {
                                "type": "job.fail_ack",
                                "job_id": job_id,
                                "status": result.status if result else "not_found",
                            }
                        )
                        if result:
                            await _db_build_jobs.cancel_downstream(job_id)
                            await emit_webhook_event(
                                "build.failed",
                                {
                                    "job_id": job_id,
                                    "recipe_name": result.recipe_name,
                                    "error": error[:256],
                                },
                                org_slug=result.org_slug,
                            )
                            if result.dag_id:
                                done = await _db_build_jobs.is_dag_complete(result.dag_id)
                                if done:
                                    summary = await _db_build_jobs.dag_summary(result.dag_id)
                                    await emit_webhook_event(
                                        "build.dag_completed",
                                        summary,
                                        org_slug=result.org_slug,
                                    )

                elif msg_type == "pong":
                    pass  # response to server ping

                else:
                    await websocket.send_json(
                        {"type": "error", "message": f"unknown type: {msg_type}"}
                    )

        except WebSocketDisconnect:
            logger.info("builder %d WebSocket disconnected", builder_id)
        except Exception:
            logger.exception("builder %d WebSocket error", builder_id)
        finally:
            _ws_builders.pop(builder_id, None)

    # ── Recipe distribution endpoints ───────────────────────

    def _require_db_recipes():
        if not _use_db or _db_recipes is None:
            raise HTTPException(
                501,
                "recipe distribution requires a database backend " "(set CVCPKG_DATABASE_URL)",
            )

    @app.post(
        "/v1/recipes/{name}",
        response_model=RecipeInfo,
        tags=["recipes"],
    )
    async def upload_recipe(
        name: str,
        file: UploadFile,
        org_slug: str = Query("", max_length=255),
        version: str = Query("", max_length=128),
        recipe_hash: str = Query("", max_length=128),
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Upload or update a recipe bundle (tar.gz).

        Admin only for global recipes.  Overwrites if the same
        ``(name, org_slug)`` already exists.
        """
        import re as _re_mod

        _require_db_recipes()
        # Validate name
        if not _re_mod.match(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$", name):
            raise HTTPException(400, "invalid recipe name")

        state = _get_state()
        recipes_dir = state.state_dir / "recipe_bundles"
        if org_slug:
            recipes_dir = recipes_dir / org_slug
        recipes_dir.mkdir(parents=True, exist_ok=True)

        dest = recipes_dir / f"{name}.tar.gz"
        # Write uploaded file
        content = await file.read()
        dest.write_bytes(content)
        bundle_size = len(content)

        info = await _db_recipes.upload(
            name=name,
            bundle_path=str(dest),
            bundle_size=bundle_size,
            uploaded_by=actor.name,
            version=version,
            recipe_hash=recipe_hash,
            org_slug=org_slug,
        )
        await _db_audit.record(
            action=AuditAction.recipe_upload,
            actor=actor.name,
            target=name,
            detail=f"version={version} org={org_slug}" if org_slug else f"version={version}",
        )
        # Notify connected builders of the new/updated recipe
        bundle_url = f"/v1/recipes/{name}"
        if org_slug:
            bundle_url += f"?org_slug={org_slug}"
        await _ws_broadcast(
            {
                "type": "recipe.push",
                "recipe": {
                    "name": name,
                    "version": version,
                    "bundle_url": bundle_url,
                },
            }
        )
        return info

    @app.get(
        "/v1/recipes",
        response_model=RecipeListResponse,
        tags=["recipes"],
    )
    async def list_recipes(
        org_slug: str | None = Query(None),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """List server-managed recipe bundles."""
        _require_db_recipes()
        recipes, total = await _db_recipes.list_recipes(
            org_slug=org_slug, limit=limit, offset=offset
        )
        return RecipeListResponse(total=total, recipes=recipes)

    @app.get(
        "/v1/recipes/{name}",
        tags=["recipes"],
    )
    async def get_recipe_bundle(
        name: str,
        org_slug: str = Query(""),
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Download a recipe bundle (tar.gz)."""
        _require_db_recipes()
        info = await _db_recipes.get(name, org_slug=org_slug)
        if info is None:
            raise HTTPException(404, f"recipe '{name}' not found")
        bundle_path = await _db_recipes.get_bundle_path(name, org_slug=org_slug)
        if bundle_path is None or not Path(bundle_path).is_file():
            raise HTTPException(404, f"recipe bundle for '{name}' not found on disk")
        return FileResponse(
            bundle_path,
            media_type="application/gzip",
            filename=f"{name}.tar.gz",
        )

    @app.delete(
        "/v1/recipes/{name}",
        tags=["recipes"],
    )
    async def delete_recipe(
        name: str,
        org_slug: str = Query(""),
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Delete a recipe bundle (admin only)."""
        _require_db_recipes()
        # Try to remove bundle file
        bundle_path = await _db_recipes.get_bundle_path(name, org_slug=org_slug)
        if bundle_path and Path(bundle_path).is_file():
            Path(bundle_path).unlink()

        deleted = await _db_recipes.delete(name, org_slug=org_slug)
        if not deleted:
            raise HTTPException(404, f"recipe '{name}' not found")
        await _db_audit.record(
            action=AuditAction.recipe_delete,
            actor=actor.name,
            target=name,
        )
        return {"ok": True, "name": name}

    # ── Webhook endpoints ──────────────────────────────────

    def _require_db_webhooks():
        if not _use_db or _db_webhooks is None:
            raise HTTPException(
                501,
                "webhooks require a database backend " "(set CVCPKG_DATABASE_URL)",
            )

    @app.post(
        "/v1/webhooks",
        response_model=WebhookInfo,
        tags=["webhooks"],
    )
    async def register_webhook(
        body: WebhookRegisterRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Register a new webhook (admin only)."""
        _require_db_webhooks()
        info = await _db_webhooks.register(
            url=body.url,
            events=body.events,
            registered_by=actor.name,
            org_slug=body.org_slug,
        )
        await _db_audit.record(
            action=AuditAction.webhook_register,
            actor=actor.name,
            target=str(info.id),
            detail=f"url={body.url}",
        )
        return info

    @app.get(
        "/v1/webhooks",
        response_model=WebhookListResponse,
        tags=["webhooks"],
    )
    async def list_webhooks(
        org_slug: str | None = Query(None),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """List registered webhooks (admin only)."""
        _require_db_webhooks()
        webhooks, total = await _db_webhooks.list_webhooks(
            org_slug=org_slug,
            limit=limit,
            offset=offset,
        )
        return WebhookListResponse(total=total, webhooks=webhooks)

    @app.get(
        "/v1/webhooks/{webhook_id}",
        response_model=WebhookInfo,
        tags=["webhooks"],
    )
    async def get_webhook(
        webhook_id: int,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Get details for a webhook (admin only)."""
        _require_db_webhooks()
        info = await _db_webhooks.get(webhook_id)
        if info is None:
            raise HTTPException(404, f"webhook {webhook_id} not found")
        return info

    @app.patch(
        "/v1/webhooks/{webhook_id}",
        response_model=WebhookInfo,
        tags=["webhooks"],
    )
    async def update_webhook(
        webhook_id: int,
        body: WebhookUpdateRequest,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Update a webhook (admin only)."""
        _require_db_webhooks()
        info = await _db_webhooks.update(
            webhook_id,
            url=body.url,
            events=body.events,
            active=body.active,
        )
        if info is None:
            raise HTTPException(404, f"webhook {webhook_id} not found")
        await _db_audit.record(
            action=AuditAction.webhook_update,
            actor=actor.name,
            target=str(webhook_id),
        )
        return info

    @app.delete(
        "/v1/webhooks/{webhook_id}",
        tags=["webhooks"],
    )
    async def delete_webhook(
        webhook_id: int,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Delete a webhook (admin only)."""
        _require_db_webhooks()
        deleted = await _db_webhooks.delete(webhook_id)
        if not deleted:
            raise HTTPException(404, f"webhook {webhook_id} not found")
        await _db_audit.record(
            action=AuditAction.webhook_delete,
            actor=actor.name,
            target=str(webhook_id),
        )
        return {"ok": True, "id": webhook_id}

    @app.post(
        "/v1/webhooks/{webhook_id}/test",
        tags=["webhooks"],
    )
    async def test_webhook(
        webhook_id: int,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Send a test payload to a webhook."""
        _require_db_webhooks()
        info = await _db_webhooks.get(webhook_id)
        if info is None:
            raise HTTPException(404, f"webhook {webhook_id} not found")
        secret = await _db_webhooks.get_secret(webhook_id)
        # Build test payload
        import hashlib as _hashlib_mod
        import hmac as _hmac_mod

        payload = json.dumps(
            {
                "event": "webhook.test",
                "webhook_id": webhook_id,
                "triggered_by": actor.name,
            }
        )
        sig = _hmac_mod.new(
            (secret or "").encode(),
            payload.encode(),
            _hashlib_mod.sha256,
        ).hexdigest()
        # Attempt delivery
        import httpx

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    info.url,
                    content=payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-CvcPkg-Signature": f"sha256={sig}",
                        "X-CvcPkg-Event": "webhook.test",
                    },
                )
            await _db_webhooks.record_delivery(webhook_id)
            return {
                "ok": True,
                "status_code": resp.status_code,
                "webhook_id": webhook_id,
            }
        except Exception as exc:
            await _db_webhooks.record_failure(webhook_id)
            raise HTTPException(502, f"delivery failed: {exc}") from exc

    # ── Retention & Quota endpoints ─────────────────────────

    @app.post(
        "/v1/admin/gc/logs",
        tags=["admin"],
    )
    async def admin_gc_logs(
        older_than_days: int = Query(30, ge=1),
        status: str | None = Query(None),
        delete_logs: bool = Query(True),
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Garbage-collect old build logs (admin only).

        Deletes logs from finished jobs older than *older_than_days*.
        Optionally filter by job status (e.g. ``failed``, ``cancelled``).
        """
        _require_db_build_jobs()
        state = _get_state()
        purged = await _db_build_jobs.purge_old_logs(
            older_than_days=older_than_days,
            logs_dir=state.logs_dir(),
            status_filter=status,
            delete_logs=delete_logs,
        )
        return {"ok": True, "purged": purged}

    @app.post(
        "/v1/admin/purge/builds",
        tags=["admin"],
    )
    async def admin_purge_builds(
        older_than_days: int = Query(30, ge=1),
        status: str | None = Query(None),
        delete_logs: bool = Query(True),
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Purge old finished build jobs and their logs (admin only).

        Deletes entire job rows (not just logs) from finished jobs
        older than *older_than_days*.
        """
        _require_db_build_jobs()
        state = _get_state()
        purged = await _db_build_jobs.purge_old_jobs(
            older_than_days=older_than_days,
            logs_dir=state.logs_dir(),
            status_filter=status,
            delete_logs=delete_logs,
        )
        return {"ok": True, "purged": purged}

    @app.get(
        "/v1/admin/quota/logs/{org_slug}",
        tags=["admin"],
    )
    async def admin_org_log_usage(
        org_slug: str,
        actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        """Get total log storage usage for an organization (admin only)."""
        _require_db_build_jobs()
        usage = await _db_build_jobs.get_org_log_usage(org_slug)
        return {"org_slug": org_slug, "log_bytes": usage}

    # ── Mirror-mode download proxy ──────────────────────────

    @app.get("/v1/mirror/download/{filename}", tags=["mirrors"])
    async def mirror_download_proxy(
        filename: str,
        _auth: TokenRecord | None = Depends(optional_reader_auth),
    ):
        """Proxy a package download from the upstream server.

        Only available when the server is running in mirror mode.
        Downloads the archive from the upstream on demand and caches
        it locally for subsequent requests.
        """
        if not MIRROR_MODE:
            raise HTTPException(404, "this endpoint is only available in mirror mode")

        state = _get_state()
        archives = state.archives_dir()
        local = archives / filename
        if local.is_file():
            return FileResponse(local, media_type="application/octet-stream")

        # Fetch from upstream
        import httpx

        upstream = MIRROR_UPSTREAM.rstrip("/")
        headers: dict[str, str] = {}
        if MIRROR_TOKEN:
            headers["Authorization"] = f"Bearer {MIRROR_TOKEN}"

        try:
            async with httpx.AsyncClient(
                timeout=300, headers=headers, follow_redirects=True
            ) as client:
                resp = await client.get(f"{upstream}/v1/download/{filename}")
                resp.raise_for_status()
                archives.mkdir(parents=True, exist_ok=True)
                tmp = local.with_suffix(".downloading")
                tmp.write_bytes(resp.content)
                tmp.rename(local)
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                exc.response.status_code,
                f"upstream returned {exc.response.status_code}",
            ) from exc
        except Exception as exc:
            raise HTTPException(502, f"failed to fetch from upstream: {exc}") from exc

        return FileResponse(local, media_type="application/octet-stream")

    return app
