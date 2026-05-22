"""FastAPI application for cvcpkg-server.

This module defines all REST API endpoints for package publishing,
serving, catalog management, token administration, and audit trail
inspection.

All mutating endpoints require a bearer token with an appropriate
role.  Read-only endpoints (GET /v1/catalog, GET /v1/packages,
GET /v1/download) are unauthenticated by default but can be locked
down via configuration.
"""

from __future__ import annotations

import hashlib
import io
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import yaml
from fastapi import Depends, FastAPI, Header, HTTPException, Query, UploadFile
from fastapi.responses import StreamingResponse

from cvcpkg import __version__
from cvcpkg.server.audit import AuditLog
from cvcpkg.server.auth import TokenStore
from cvcpkg.server.models import (
    AuditAction,
    AuditLogResponse,
    CatalogResponse,
    HealthResponse,
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


def _get_state() -> ServerState:
    if _state is None:
        raise RuntimeError("server not initialised")
    return _state


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

    def _dep(authorization: str | None = Header(None)) -> TokenRecord:
        state = _get_state()
        raw = _extract_token(authorization)
        if raw is None:
            raise HTTPException(401, "missing Authorization header")
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


def optional_reader_auth(authorization: str | None = Header(None)) -> TokenRecord | None:
    """For read endpoints: enforce auth only if configured."""
    state = _get_state()
    if not state.require_auth_for_reads:
        return None
    raw = _extract_token(authorization)
    if raw is None:
        raise HTTPException(401, "this server requires authentication for reads")
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
        global _state, _START_TIME
        _START_TIME = time.monotonic()
        sd = state_dir or Path(os.environ.get("CVCPKG_SERVER_STATE_DIR", "/var/lib/cvcpkg-server"))
        _state = ServerState(
            sd,
            storage_uri=storage_uri,
            require_auth_for_reads=require_auth_for_reads,
        )
        yield
        _state = None

    app = FastAPI(
        title="cvcpkg-server",
        version=__version__,
        description="Package server for libcvc-deps component bundles",
        lifespan=lifespan,
    )

    # ── Health ──────────────────────────────────────────────

    @app.get("/healthz", response_model=HealthResponse, tags=["health"])
    async def healthz():
        state = _get_state()
        return HealthResponse(
            version=__version__,
            storage_scheme=(
                state.storage_uri.split("://")[0] if "://" in state.storage_uri else "file"
            ),
            packages_count=len(state.index.get("bundles", [])),
            uptime_seconds=round(time.monotonic() - _START_TIME, 2),
        )

    # ── Catalog (read) ──────────────────────────────────────

    @app.get("/v1/catalog", response_model=CatalogResponse, tags=["catalog"])
    async def get_catalog(_auth: None = Depends(optional_reader_auth)):
        state = _get_state()
        return CatalogResponse(
            revision=state.index.get("revision", 0),
            bundles=state.index.get("bundles", []),
        )

    # ── Packages (read) ─────────────────────────────────────

    @app.get("/v1/packages", response_model=PackageListResponse, tags=["packages"])
    async def list_packages(
        name: str = Query("", description="Filter by component name"),
        platform: str = Query("", description="Filter by platform"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        _auth: None = Depends(optional_reader_auth),
    ):
        state = _get_state()
        bundles = state.index.get("bundles", [])
        if name:
            bundles = [b for b in bundles if b.get("name") == name]
        if platform:
            bundles = [b for b in bundles if b.get("platform") == platform]
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
        _auth: None = Depends(optional_reader_auth),
    ):
        state = _get_state()
        bundles = [b for b in state.index.get("bundles", []) if b.get("name") == name]
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
        file: UploadFile,
        name: str = Query(..., description="Component name"),
        version: str = Query(..., description="Component version"),
        platform: str = Query("", description="Target platform"),
        arch: str = Query("", description="Target architecture"),
        build_type: str = Query("release"),
        link: str = Query("shared"),
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
        state = _get_state()

        # Check for duplicates
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
                    f"{name}=={version} ({platform}/{arch}/{build_type}/{link}) already published. "
                    "Yank the existing version first, or use a new revision.",
                )

        # Read and hash the upload
        h = hashlib.sha256()
        content = await file.read()
        h.update(content)
        sha256 = h.hexdigest()
        size_bytes = len(content)

        # Store the archive
        safe_filename = f"{name}-{version}-{platform}-{arch}-{build_type}-{link}.tar.zst"
        # Remove any unsafe characters
        safe_filename = "".join(
            c if c in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-+" else "_"
            for c in safe_filename
        )
        dest = state.archives_dir() / safe_filename
        dest.write_bytes(content)

        # Update the index
        import datetime

        archive_url = f"/v1/download/{safe_filename}"
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
        }
        state.index.setdefault("bundles", []).append(bundle)
        state.save_index()

        # Audit
        state.audit.record(
            action=AuditAction.publish,
            actor=actor.name,
            target=f"{name}=={version}",
            detail=f"platform={platform} arch={arch} sha256={sha256}",
        )

        return PublishResponse(
            name=name,
            version=version,
            sha256=sha256,
            archive_url=archive_url,
        )

    # ── Yank / Unyank (write) ──────────────────────────────

    @app.post("/v1/packages/{name}/{version}/yank", tags=["publish"])
    async def yank(
        name: str,
        version: str,
        actor: TokenRecord = Depends(require_role(TokenRole.publisher, TokenRole.admin)),
    ):
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
        state = _get_state()
        entries, total = state.audit.entries(
            limit=limit, offset=offset, action=action, target=target
        )
        return AuditLogResponse(entries=entries, total=total)

    @app.get("/v1/audit/verify", tags=["audit"])
    async def verify_audit_chain(
        _actor: TokenRecord = Depends(require_role(TokenRole.admin)),
    ):
        state = _get_state()
        ok, message = state.audit.verify_chain()
        status_code = 200 if ok else 409
        return {"ok": ok, "message": message}

    return app
