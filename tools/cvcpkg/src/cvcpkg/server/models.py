"""Pydantic models for the cvcpkg-server REST API."""

from __future__ import annotations

import datetime
from enum import Enum

from pydantic import BaseModel, Field

# ── Enums ───────────────────────────────────────────────────────


class TokenRole(str, Enum):
    """Authorization roles for API tokens."""

    reader = "reader"
    publisher = "publisher"
    admin = "admin"


class AuditAction(str, Enum):
    """Actions recorded in the audit trail."""

    publish = "publish"
    yank = "yank"
    unyank = "unyank"
    delete = "delete"
    token_create = "token_create"
    token_revoke = "token_revoke"
    catalog_rebuild = "catalog_rebuild"


# ── Token management ───────────────────────────────────────────


class TokenRecord(BaseModel):
    """Persisted token metadata (the secret is never stored)."""

    name: str
    role: TokenRole
    token_hash: str = Field(description="HMAC-SHA256 hash of the bearer token")
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    expires_at: datetime.datetime | None = None
    revoked: bool = False


class TokenCreateRequest(BaseModel):
    name: str
    role: TokenRole = TokenRole.publisher
    expires_in_days: int | None = None


class TokenCreateResponse(BaseModel):
    name: str
    role: TokenRole
    token: str = Field(description="Bearer token — shown only once")
    expires_at: datetime.datetime | None = None


# ── Package / catalog ──────────────────────────────────────────


class PackageInfo(BaseModel):
    """Summary of a published bundle."""

    name: str
    version: str
    platform: str
    arch: str
    build_type: str
    link: str
    sha256: str
    size_bytes: int
    archive_url: str
    published_at: datetime.datetime
    yanked: bool = False
    signature: str = ""
    key_fingerprint: str = ""
    release_tag: str = Field(
        default="",
        description=(
            "cvcpkg release this package belongs to (e.g. 'v1.3.0'). "
            "Empty string means the package is a live/updated build "
            "that is not yet part of an official release."
        ),
    )
    recipe_version: str = Field(
        default="",
        description=(
            "Recipe revision that produced this build (e.g. commit SHA or "
            "recipe file hash).  When this differs from the release recipe "
            "version, the package is divergent from the official release."
        ),
    )
    description: str = ""
    homepage: str = ""
    license: str = ""
    maintainer: str = ""
    tags: str = ""


class PublishResponse(BaseModel):
    name: str
    version: str
    sha256: str
    archive_url: str
    message: str = "published"


class CatalogResponse(BaseModel):
    schema_version: int = 1
    revision: int
    bundles: list[dict]


class PackageListResponse(BaseModel):
    total: int
    packages: list[PackageInfo]


# ── Audit trail ─────────────────────────────────────────────────


class AuditEntry(BaseModel):
    """One row of the append-only audit log."""

    id: int = 0
    timestamp: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    action: AuditAction
    actor: str = Field(description="Token name that performed the action")
    target: str = Field(description="Component name or token name affected")
    detail: str = ""
    prev_sha256: str = Field(
        default="",
        description="SHA-256 of the previous audit entry for tamper detection",
    )


class AuditLogResponse(BaseModel):
    entries: list[AuditEntry]
    total: int


# ── Health ──────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str
    storage_scheme: str
    packages_count: int
    uptime_seconds: float
