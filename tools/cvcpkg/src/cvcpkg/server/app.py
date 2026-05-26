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

import hashlib
import logging
import os
import secrets
import signal
import tempfile
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse

from cvcpkg import __version__
from cvcpkg.server.audit import AuditLog
from cvcpkg.server.auth import TokenStore
from cvcpkg.server.models import (
    AuditAction,
    AuditLogResponse,
    CatalogResponse,
    HealthResponse,
    OrgCreateRequest,
    OrgDetailResponse,
    OrgInfo,
    OrgListResponse,
    OrgRole,
    OrgUpdateRequest,
    PackageInfo,
    PackageListResponse,
    PublishResponse,
    TokenCreateRequest,
    TokenCreateResponse,
    TokenRecord,
    TokenRole,
)

# ── State ───────────────────────────────────────────────────────

_INDEX_FILE = "index.yaml"
_ARCHIVES_DIR = "archives"
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

# CORS allowed origins (comma-separated, empty = deny all cross-origin)
CORS_ALLOWED_ORIGINS = [
    o.strip() for o in os.environ.get("CVCPKG_CORS_ORIGINS", "").split(",") if o.strip()
]

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


# Singleton — set by create_app() or lifespan
_state: ServerState | None = None
_use_db: bool = False
_db_tokens = None  # DbTokenStore when using DB backend
_db_audit = None  # DbAuditLog when using DB backend
_db_packages = None  # DbPackageIndex when using DB backend
_db_orgs = None  # DbOrgStore when using DB backend


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


# ── App factory ─────────────────────────────────────────────────


def create_app(
    state_dir: Path | None = None,
    storage_uri: str = "",
    require_auth_for_reads: bool = False,
) -> FastAPI:
    """Build and return the FastAPI application."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        global _state, _START_TIME, _use_db, _db_tokens, _db_audit, _db_packages, _db_orgs
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
            from cvcpkg.server.db_stores import DbAuditLog, DbOrgStore, DbPackageIndex, DbTokenStore

            init_db(db_url)
            await create_tables()
            _db_tokens = DbTokenStore(sd)
            _db_audit = DbAuditLog()
            _db_packages = DbPackageIndex()
            _db_orgs = DbOrgStore()
            _use_db = True
            # Still need ServerState for archives dir and storage_uri
            _state = ServerState(
                sd,
                storage_uri=storage_uri,
                require_auth_for_reads=require_auth_for_reads,
            )
            yield
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
        )

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

    @app.get("/v1/catalog", response_model=CatalogResponse, tags=["catalog"])
    async def get_catalog(_auth: None = Depends(optional_reader_auth)):
        if _use_db:
            cat = await _db_packages.get_catalog_dict()
            return CatalogResponse(
                revision=cat.get("revision", 0),
                bundles=cat.get("bundles", []),
            )
        state = _get_state()
        return CatalogResponse(
            revision=state.index.get("revision", 0),
            bundles=state.index.get("bundles", []),
        )

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
            build_deps = r.raw.get("depends", {}).get("build", [])
            names: list[str] = []
            for d in build_deps:
                if isinstance(d, str):
                    names.append(d)
                elif isinstance(d, dict):
                    names.append(d["name"])
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
        release: str = Query(
            "",
            description=(
                "Filter by release tag (e.g. 'v1.3.0').  Use 'live' to see "
                "only packages not yet in any release."
            ),
        ),
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
        if release == "live":
            bundles = [b for b in bundles if not b.get("release_tag")]
        elif release:
            bundles = [b for b in bundles if b.get("release_tag") == release]
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
                yanked=b.get("yanked", False),
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
            )
            for b in bundles
        ]
        return PackageListResponse(total=len(packages), packages=packages)

    # ── Download (read) ─────────────────────────────────────

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
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        _check_rate_limit(request)
        state = _get_state()

        # Validate org membership and storage limit if publishing to an org
        if org and _use_db and _db_orgs is not None:
            org_info = await _db_orgs.get(org)
            if org_info is None:
                raise HTTPException(404, f"organization '{org}' not found")
            if not await _db_orgs.is_member(org, actor.name):
                raise HTTPException(403, f"you are not a member of organization '{org}'")

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
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        """Initialise a chunked upload session.

        Returns an ``upload_id`` and recommended ``chunk_size``.
        Upload chunks with ``PATCH /v1/upload/{upload_id}``, then
        finalise with ``POST /v1/upload/{upload_id}/complete``.
        """
        _check_rate_limit(request)
        _purge_expired_sessions()
        state = _get_state()

        if total_size > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413, f"declared size {total_size} exceeds maximum {MAX_UPLOAD_BYTES} bytes"
            )

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
        state = _get_state()
        if _use_db:
            raw = await _db_tokens.create(
                name=req.name,
                role=req.role,
                expires_in_days=req.expires_in_days,
            )
            await _db_audit.record(
                action=AuditAction.token_create,
                actor=actor.name,
                target=req.name,
                detail=f"role={req.role.value}",
            )
            record = await _db_tokens.verify(raw)
        else:
            raw = state.tokens.create(
                name=req.name,
                role=req.role,
                expires_in_days=req.expires_in_days,
            )
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
                    "created_at": t.created_at.isoformat(),
                    "expires_at": t.expires_at.isoformat() if t.expires_at else None,
                    "revoked": t.revoked,
                }
                for t in tokens
            ]
        }

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
        try:
            org = await _db_orgs.create(
                slug=body.slug,
                display_name=body.display_name,
                description=body.description,
                logo_url=body.logo_url,
                homepage=body.homepage,
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
        orgs, total = await _db_orgs.list_orgs(limit=limit, offset=offset)
        return OrgListResponse(total=total, organizations=orgs)

    @app.get("/v1/orgs/{slug}", response_model=OrgDetailResponse, tags=["organizations"])
    async def get_org(slug: str):
        if not _use_db or _db_orgs is None:
            raise HTTPException(404, "organization not found")
        org = await _db_orgs.get(slug)
        if org is None:
            raise HTTPException(404, f"organization '{slug}' not found")
        members = await _db_orgs.get_members(slug)
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
        updated = await _db_orgs.update(
            slug,
            display_name=body.display_name,
            description=body.description,
            logo_url=body.logo_url,
            homepage=body.homepage,
        )
        if updated is None:
            raise HTTPException(404, f"organization '{slug}' not found")
        await _db_audit.record(
            action=AuditAction.org_update,
            actor=actor.name,
            target=slug,
        )
        return updated

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

    # ── Org HTML pages ──────────────────────────────────────

    @app.get("/orgs", response_class=HTMLResponse, include_in_schema=False)
    async def orgs_listing_page():
        from cvcpkg.server.landing import orgs_listing_html

        return HTMLResponse(orgs_listing_html())

    @app.get("/org/{slug}", response_class=HTMLResponse, include_in_schema=False)
    async def org_detail_page(slug: str):
        from cvcpkg.server.landing import org_detail_html

        return HTMLResponse(org_detail_html(slug))

    return app
