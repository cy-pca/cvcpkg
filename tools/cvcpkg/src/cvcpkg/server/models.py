"""Pydantic models for the cvcpkg-server REST API."""

from __future__ import annotations

import datetime
import re
from enum import Enum

from pydantic import BaseModel, Field

# ── Org slug validation (GitHub username rules) ─────────────────

_ORG_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
_CONSECUTIVE_HYPHENS_RE = re.compile(r"--")


def validate_org_slug(slug: str) -> str | None:
    """Validate an organization slug using GitHub username rules.

    Rules (matching GitHub):
    - 1–39 characters
    - Only lowercase alphanumeric characters or hyphens
    - Cannot start or end with a hyphen
    - No consecutive hyphens

    Returns ``None`` on success or an error message string on failure.
    """
    if not slug:
        return "organization slug must not be empty"
    if len(slug) > 39:
        return f"organization slug must be at most 39 characters (got {len(slug)})"
    if _CONSECUTIVE_HYPHENS_RE.search(slug):
        return "organization slug must not contain consecutive hyphens"
    if not _ORG_SLUG_RE.match(slug):
        return (
            "organization slug may only contain lowercase alphanumeric "
            "characters or hyphens, and cannot start or end with a hyphen"
        )
    return None


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
    org_create = "org_create"
    org_add_member = "org_add_member"
    org_remove_member = "org_remove_member"
    org_update = "org_update"


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
    org: str = Field(
        default="",
        description=(
            "Organization slug that owns the package.  Empty for " "official/public base packages."
        ),
    )

    @property
    def qualified_name(self) -> str:
        """Return ``org/name`` for org packages, plain ``name`` otherwise."""
        return f"{self.org}/{self.name}" if self.org else self.name


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


# ── Organizations ───────────────────────────────────────────────


class OrgRole(str, Enum):
    """Membership roles within an organization."""

    owner = "owner"
    member = "member"


class OrgInfo(BaseModel):
    """Summary of an organization."""

    slug: str
    display_name: str
    description: str = ""
    logo_url: str = ""
    homepage: str = ""
    is_private: bool = False
    storage_limit_bytes: int = 10 * 1024 * 1024 * 1024
    storage_used_bytes: int = 0
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    created_by: str = ""


class OrgMember(BaseModel):
    """An organization membership record."""

    token_name: str
    role: OrgRole = OrgRole.member
    added_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class OrgCreateRequest(BaseModel):
    slug: str = Field(
        ...,
        min_length=1,
        max_length=39,
        pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$",
        description=(
            "Organization slug (GitHub username rules): 1–39 chars, "
            "lowercase alphanumeric or hyphens, no leading/trailing/consecutive hyphens."
        ),
    )
    display_name: str = Field(..., min_length=1, max_length=255)
    description: str = ""
    logo_url: str = ""
    homepage: str = ""
    is_private: bool = False


class OrgUpdateRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    logo_url: str | None = None
    homepage: str | None = None
    is_private: bool | None = None


class OrgDetailResponse(BaseModel):
    org: OrgInfo
    members: list[OrgMember]
    packages: list[PackageInfo]


class OrgListResponse(BaseModel):
    total: int
    organizations: list[OrgInfo]
