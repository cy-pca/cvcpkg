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
    admin_settings_update = "admin_settings_update"
    cache_gc = "cache_gc"
    tag_create = "tag_create"
    tag_update = "tag_update"
    tag_delete = "tag_delete"
    mirror_register = "mirror_register"
    mirror_reject = "mirror_reject"
    mirror_remove = "mirror_remove"
    registration_request = "registration_request"
    registration_approve = "registration_approve"
    registration_deny = "registration_deny"
    token_update_email = "token_update_email"
    token_update_profile = "token_update_profile"


# ── Token management ───────────────────────────────────────────


class TokenRecord(BaseModel):
    """Persisted token metadata (the secret is never stored)."""

    name: str
    role: TokenRole
    token_hash: str = Field(description="HMAC-SHA256 hash of the bearer token")
    email: str = ""
    description: str = ""
    metadata: str = ""
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    expires_at: datetime.datetime | None = None
    revoked: bool = False


class TokenCreateRequest(BaseModel):
    name: str
    role: TokenRole = TokenRole.publisher
    expires_in_days: int | None = None
    email: str = ""
    description: str = ""
    metadata: str = ""


class TokenCreateResponse(BaseModel):
    name: str
    role: TokenRole
    token: str = Field(description="Bearer token — shown only once")
    expires_at: datetime.datetime | None = None


class EmailUpdateRequest(BaseModel):
    email: str


class ProfileUpdateRequest(BaseModel):
    """Update a user's profile fields."""

    description: str | None = Field(None, description="User description (unicode)")
    metadata: str | None = Field(None, description="Arbitrary JSON or text metadata")


class UserProfileResponse(BaseModel):
    """Public user profile information."""

    name: str
    role: str
    email: str = ""
    description: str = ""
    metadata: str = ""
    created_at: datetime.datetime


# ── Registration ───────────────────────────────────────────────


class RegistrationMode(str, Enum):
    """Server registration policy."""

    open = "open"
    admin_gated = "admin-gated"


class RegistrationRequest(BaseModel):
    """Self-service token registration request."""

    name: str
    email: str
    role: TokenRole = TokenRole.reader
    description: str = ""
    metadata: str = ""


class RegistrationResponse(BaseModel):
    """Response to a registration request."""

    message: str
    token: str | None = Field(
        None, description="Bearer token (only set in open registration mode)"
    )
    request_id: int | None = Field(
        None, description="Pending request ID (only set in admin-gated mode)"
    )


class TokenRequestStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    denied = "denied"


class TokenRequestRecord(BaseModel):
    """A pending or resolved token registration request."""

    id: int
    name: str
    email: str
    role: TokenRole
    status: TokenRequestStatus = TokenRequestStatus.pending
    reviewed_by: str = ""
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    resolved_at: datetime.datetime | None = None


class TokenRequestListResponse(BaseModel):
    requests: list[TokenRequestRecord]
    total: int


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
    published_by: str = Field(
        default="",
        description="Token name of the user who published this package.",
    )
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


class CacheStatusResponse(BaseModel):
    """Response for the ``/v1/cache/status`` probe endpoint."""

    hit: bool
    name: str = ""
    version: str = ""
    chain_hash: str = ""
    platform: str = ""
    arch: str = ""
    build_type: str = ""
    link: str = ""
    archive_url: str = ""
    sha256: str = ""
    size_bytes: int = 0
    org: str = ""
    published_at: datetime.datetime | None = None


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
    mirror_mode: bool = False


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
    storage_limit_bytes: int | None = Field(
        None,
        ge=0,
        description="Per-org storage cap in bytes (admin-only).",
    )


class OrgDetailResponse(BaseModel):
    org: OrgInfo
    members: list[OrgMember]
    packages: list[PackageInfo]


class OrgListResponse(BaseModel):
    total: int
    organizations: list[OrgInfo]


# ── Tags ────────────────────────────────────────────────────────


class TagInfo(BaseModel):
    """Curated tag metadata for the browse-by-tag front page."""

    name: str
    org_slug: str = ""
    display_name: str = ""
    description: str = ""
    logo_url: str = ""
    package_count: int = 0
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    created_by: str = ""

    @property
    def qualified_name(self) -> str:
        """Return ``org/name`` for org tags, plain ``name`` otherwise."""
        return f"{self.org_slug}/{self.name}" if self.org_slug else self.name


class TagCreateRequest(BaseModel):
    name: str = Field(
        ...,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9](?:[a-z0-9._-]*[a-z0-9])?$",
        description="Tag name (lowercase alphanumeric, dots, hyphens, underscores).",
    )
    org_slug: str = Field(
        default="",
        max_length=64,
        description="Organization slug.  Empty for global tags.",
    )
    display_name: str = Field(default="", max_length=255)
    description: str = ""
    logo_url: str = Field(default="", max_length=512)


class TagUpdateRequest(BaseModel):
    display_name: str | None = None
    description: str | None = None
    logo_url: str | None = None


class TagListResponse(BaseModel):
    total: int
    tags: list[TagInfo]


# ── Mirrors ─────────────────────────────────────────────────────


class MirrorInfo(BaseModel):
    """A registered mirror server."""

    url: str = Field(description="Base URL of the mirror (e.g. https://mirror.example.com)")
    display_name: str = ""
    contact: str = Field(
        default="",
        description="Operator contact email or URL.",
    )
    registered_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    last_health_check: datetime.datetime | None = None
    last_healthy_at: datetime.datetime | None = None
    healthy: bool = True
    consecutive_failures: int = 0
    rejected: bool = False
    rejected_at: datetime.datetime | None = None
    rejected_by: str = ""
    packages_count: int = 0


class MirrorRegisterRequest(BaseModel):
    """Request body when a mirror registers itself with the primary."""

    url: str = Field(
        ...,
        min_length=8,
        max_length=2048,
        description="Public base URL of the mirror.",
    )
    display_name: str = Field(default="", max_length=255)
    contact: str = Field(default="", max_length=255)


class MirrorListResponse(BaseModel):
    total: int
    mirrors: list[MirrorInfo]
