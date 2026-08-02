# SPDX-License-Identifier: MIT
# Copyright (c) 2026 CyberPC Angel, LLC

"""Pydantic models for the cvcpkg-server REST API."""

from __future__ import annotations

import datetime
from enum import Enum

from pydantic import BaseModel, Field

# Moved to the dependency-free cvcpkg.orgs so the CLI can validate slugs
# without the server extras; re-exported here for backward compatibility.
from cvcpkg.orgs import validate_org_slug  # noqa: F401

# ── Org slug validation (GitHub username rules) ─────────────────

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
    # Irreversible removal of a yanked bundle's row AND archive bytes, whether
    # by an admin's `cvcpkg nuke` or by the yank-retention GC (recorded with
    # actor "retention-gc", which holds no token).
    nuke = "nuke"
    token_create = "token_create"
    token_revoke = "token_revoke"
    catalog_rebuild = "catalog_rebuild"
    org_create = "org_create"
    org_add_member = "org_add_member"
    org_remove_member = "org_remove_member"
    org_update = "org_update"
    admin_settings_update = "admin_settings_update"
    backup = "backup"
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
    token_rotate = "token_rotate"
    builder_register = "builder_register"
    builder_unregister = "builder_unregister"
    builder_update = "builder_update"
    build_submit = "build_submit"
    build_cancel = "build_cancel"
    build_claim = "build_claim"
    build_complete = "build_complete"
    build_fail = "build_fail"
    recipe_upload = "recipe_upload"
    recipe_delete = "recipe_delete"
    webhook_register = "webhook_register"
    webhook_update = "webhook_update"
    webhook_delete = "webhook_delete"
    webhook_delivery_failed = "webhook_delivery_failed"
    # Manual operator correction of package metadata (e.g. fixing a
    # catalog row's sha256/size to match the stored archive).
    package_integrity_fix = "package_integrity_fix"


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
    # Rotation grace window: the pre-rotation secret's hash stays valid
    # until previous_hash_expires_at so callers can swap stored secrets
    # (CI variables, requirement files) without an outage.
    previous_token_hash: str = ""
    previous_hash_expires_at: datetime.datetime | None = None
    # Transient (never persisted): True when this verification was
    # satisfied by the pre-rotation grace hash rather than the current
    # secret. Rotation is denied to such callers — otherwise a leaked
    # old secret could re-rotate inside the window and steal the token.
    via_previous_hash: bool = Field(default=False, exclude=True)


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


class TokenRotateRequest(BaseModel):
    grace_minutes: int = Field(
        default=0,
        ge=0,
        le=10080,  # one week
        description=(
            "How long the pre-rotation secret keeps working after rotation. "
            "0 kills it immediately."
        ),
    )


class TokenRotateResponse(BaseModel):
    name: str
    role: TokenRole
    token: str = Field(description="New bearer token — shown only once")
    expires_at: datetime.datetime | None = None
    previous_valid_until: datetime.datetime | None = Field(
        default=None,
        description="When the pre-rotation secret stops working (None = immediately)",
    )


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
    packages_published: int = 0
    created_at: datetime.datetime


class UserListResponse(BaseModel):
    """Paginated list of user profiles."""

    users: list[UserProfileResponse]
    total: int


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
    token: str | None = Field(None, description="Bearer token (only set in open registration mode)")
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
    # Set when yanked, cleared on unyank.  Exposed so clients can show when a
    # bundle was retired and when yank retention will purge it.
    yanked_at: datetime.datetime | None = None
    # What the upstream this bundle was mirrored from last said.  Normally
    # equal to ``yanked``; they differ when a mirror operator deliberately
    # unyanked something upstream still considers retired.  Clients honour
    # upstream by default and need this to see the disagreement at all.
    upstream_yanked: bool = False
    # True when this locally-published public bundle diverges from the populate
    # upstream: its coordinates exist upstream with a different sha256, so the
    # local build shadows and disagrees with the canonical upstream package.
    # The SPA shows a warning symbol; an admin resolves it by nuking the local
    # bundle so upstream re-populates.
    diverges_upstream: bool = False
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
    published_by_email: str = Field(
        default="",
        description="Email of the user who published this package.",
    )
    org: str = Field(
        default="",
        description=(
            "Organization slug that owns the package.  Empty for official/public base packages."
        ),
    )
    required_deps: list = Field(
        default_factory=list,
        description=(
            "Runtime dependencies as dicts [{name, version, org?, server?}, ...]. "
            "`server` names a federated registry host (see cvcpkg.refs)."
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
    # Non-fatal advisory returned to the publisher — e.g. a local public
    # publish on an edge/satellite cluster that diverges from and shadows the
    # canonical upstream package.  Empty when there is nothing to warn about.
    warning: str = ""


class CatalogResponse(BaseModel):
    schema_version: int = 1
    revision: int
    bundles: list[dict]


class PackageListResponse(BaseModel):
    total: int
    packages: list[PackageInfo]


class FacetBucket(BaseModel):
    """One value + document count for a search facet."""

    value: str
    count: int


class SearchFacets(BaseModel):
    """Aggregated facets over the search-matching (but unpaginated) result set."""

    platforms: list[FacetBucket] = Field(default_factory=list)
    archs: list[FacetBucket] = Field(default_factory=list)
    build_types: list[FacetBucket] = Field(default_factory=list)
    links: list[FacetBucket] = Field(default_factory=list)
    releases: list[FacetBucket] = Field(default_factory=list)
    orgs: list[FacetBucket] = Field(default_factory=list)
    tags: list[FacetBucket] = Field(default_factory=list)
    licenses: list[FacetBucket] = Field(default_factory=list)


class SearchResponse(BaseModel):
    """Response for the ``/v1/search`` endpoint."""

    total: int = Field(description="Total number of matching bundle rows.")
    package_count: int = Field(
        default=0,
        description="Total number of distinct package names matching the query.",
    )
    total_size_bytes: int = Field(
        default=0,
        description="Sum of size_bytes across the entire matching result set.",
    )
    packages: list[PackageInfo]
    limit: int = 0
    offset: int = 0
    query: str = ""
    facets: SearchFacets = Field(default_factory=SearchFacets)


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
    populate_upstream: str = Field(
        default="",
        description=(
            "Upstream server this instance auto-populates missing packages "
            "from (CVCPKG_POPULATE_UPSTREAM).  Empty when disabled."
        ),
    )
    populate_stats: dict = Field(
        default_factory=dict,
        description="Rolling stats of the populate loop (last_sync, counts, last_error).",
    )


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
    # Storage figures are member/super-admin-only; the API nulls them for
    # non-members so the SPA simply omits the storage UI.
    storage_limit_bytes: int | None = 10 * 1024 * 1024 * 1024
    storage_used_bytes: int | None = 0
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


# ── Builder (remote build agent) ──────────────────────────────


class BuilderStatus(str, Enum):
    """Runtime status of a registered builder."""

    online = "online"
    offline = "offline"
    busy = "busy"


class BuilderInfo(BaseModel):
    """Public representation of a registered builder."""

    id: int
    name: str
    org_slug: str = ""
    served_namespaces: list[str] = Field(
        default_factory=list,
        description="Namespaces this builder serves ('' = public); always contains org_slug.",
    )
    platform: str
    arch: str
    labels: list[str] = Field(default_factory=list)
    capabilities: dict = Field(default_factory=dict)
    status: str = BuilderStatus.offline
    current_jobs: int = 0
    max_jobs: int = 1
    prefer_affinity: bool = False
    last_heartbeat: datetime.datetime | None = None
    registered_by: str = ""
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class BuilderRegisterRequest(BaseModel):
    """Request body for registering a new builder."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$",
        description="Human-readable builder name (unique per org).",
    )
    org_slug: str = Field(
        default="",
        max_length=255,
        description="Home namespace / identity (empty = public global builder).",
    )
    served_namespaces: list[str] = Field(
        default_factory=list,
        description=(
            "Namespaces this builder accepts jobs for ('' = public). Empty means "
            "just the home org_slug; org_slug is always included."
        ),
    )
    platform: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Builder platform (linux, macos, windows, freebsd, …).",
    )
    arch: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Builder architecture (x86_64, arm64, riscv64, …).",
    )
    labels: list[str] = Field(
        default_factory=list,
        description="Arbitrary labels for builder selection.",
    )
    capabilities: dict = Field(
        default_factory=dict,
        description="Supported link modes, configs, etc.",
    )
    max_jobs: int = Field(
        default=1,
        ge=1,
        le=256,
        description="Maximum concurrent jobs (default: 1).",
    )
    prefer_affinity: bool = Field(
        default=False,
        description="Prefer this builder for recipes it has previously built.",
    )


class BuilderUpdateRequest(BaseModel):
    """Partial update for a builder's mutable fields."""

    labels: list[str] | None = None
    capabilities: dict | None = None
    max_jobs: int | None = Field(None, ge=1, le=256)
    prefer_affinity: bool | None = None
    served_namespaces: list[str] | None = Field(
        None,
        description="Replace the served-namespace set ('' = public); org_slug stays included.",
    )


class BuilderHeartbeatRequest(BaseModel):
    """Heartbeat payload from a running builder."""

    status: str = Field(
        default=BuilderStatus.online,
        description="Current builder status.",
    )
    current_jobs: int = Field(
        default=0,
        ge=0,
        description="Number of jobs currently running.",
    )


class BuilderListResponse(BaseModel):
    total: int
    builders: list[BuilderInfo]


# ── Build Job models ────────────────────────────────────────────


class BuildJobStatus(str, Enum):
    """Lifecycle status of a build job."""

    pending = "pending"
    dispatched = "dispatched"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"
    timed_out = "timed_out"
    paused = "paused"
    unschedulable = "unschedulable"


class BuildJobAlreadyClaimedError(Exception):
    """A worker tried to claim a job another worker already holds.

    Kept distinct from "job not found" so the API can answer 409 rather than
    404 -- and, above all, rather than handing the loser the job anyway, which
    is what let two workers build the same variant concurrently.

    Lives here rather than in db_stores so app.py can catch it without pulling
    in the SQLAlchemy layer, which it imports lazily.
    """

    def __init__(self, job_id: int, *, holder: str = "", builder_id: int | None = None):
        self.job_id = job_id
        self.holder = holder
        self.builder_id = builder_id
        who = holder or (f"builder #{builder_id}" if builder_id is not None else "another worker")
        super().__init__(f"build job {job_id} is already claimed by {who}")


class BuildJobInfo(BaseModel):
    """Public representation of a build job."""

    id: int
    dag_id: str | None = None
    org_slug: str = ""
    recipe_name: str
    recipe_version: str = ""
    recipe_hash: str = ""
    platform: str
    arch: str
    config: str = "release"
    link: str = "shared"
    required_capabilities: list[str] = Field(
        default_factory=list,
        description="Builder capabilities this job needs (e.g. ['cuda']).",
    )
    builder_id: int | None = None
    claimed_by: str = ""
    status: str = BuildJobStatus.pending
    priority: int = 0
    timeout_seconds: int | None = None
    submitted_by: str = ""
    submitted_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None
    log_url: str | None = None
    log_size_bytes: int | None = None
    error_message: str | None = None
    result_archive_url: str | None = None
    depends_on: list[int] = Field(default_factory=list)


class BuildJobSubmitRequest(BaseModel):
    """Request body for submitting a single build job."""

    recipe_name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Recipe to build.",
    )
    recipe_version: str = Field(
        default="",
        max_length=128,
        description="Recipe version.",
    )
    recipe_hash: str = Field(
        default="",
        max_length=128,
        description="Chain hash for cache dedup.",
    )
    platform: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Target platform.",
    )
    arch: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Target architecture.",
    )
    config: str = Field(
        default="release",
        description="Build config (release or debug).",
    )
    link: str = Field(
        default="shared",
        description="Link mode (shared or static).",
    )
    required_capabilities: list[str] = Field(
        default_factory=list,
        max_length=16,
        description=(
            "Builder capabilities this job needs, from the recipe's top-level "
            "requires_capabilities (e.g. ['cuda']).  The scheduler dispatches "
            "the job only to a builder advertising ALL of them."
        ),
    )
    org_slug: str = Field(
        default="",
        max_length=255,
        description="Organization scope.",
    )
    priority: int = Field(
        default=0,
        ge=0,
        description="Job priority (higher = runs first).",
    )
    timeout_seconds: int | None = Field(
        default=None,
        ge=60,
        le=172800,
        description="Per-job timeout override (60s–48h).",
    )
    depends_on: list[int] = Field(
        default_factory=list,
        description="IDs of prerequisite jobs.",
    )


class DagSubmitRequest(BaseModel):
    """Request body for submitting a DAG of build jobs."""

    jobs: list[BuildJobSubmitRequest] = Field(
        ...,
        min_length=1,
        description="Ordered list of jobs forming the DAG.",
    )
    dag_id: str | None = Field(
        default=None,
        max_length=64,
        description="Optional DAG group ID (auto-generated if omitted).",
    )


class DagSubmitResponse(BaseModel):
    """Response from DAG submission."""

    dag_id: str
    total: int
    jobs: list[BuildJobInfo]


class BuildJobListResponse(BaseModel):
    total: int
    jobs: list[BuildJobInfo]


class BuildJobClaimRequest(BaseModel):
    """A worker claims a pending or dispatched job.

    ``builder_id`` is optional so a platform with no persistent builder can
    still drain its queue.  macOS is the motivating case: GitHub-hosted
    runners are ephemeral, and registering one as a builder per drain leaves
    a dead registration behind for a machine that no longer exists.  Such a
    worker claims anonymously and identifies itself with ``claimant``.
    """

    builder_id: int | None = Field(
        None,
        description=(
            "ID of the registered builder claiming the job.  Omit for an "
            "unregistered worker (e.g. an ephemeral CI runner draining the "
            "queue); pass claimant instead."
        ),
    )
    claimant: str = Field(
        "",
        max_length=255,
        description=(
            "Free-form identity of an unregistered worker, e.g. "
            "'gha-run-29372085620'.  Recorded for traceability so a running "
            "job is never anonymous."
        ),
    )


class BuildJobCompleteRequest(BaseModel):
    """Builder reports job completion."""

    result_archive_url: str = Field(
        default="",
        description="URL of the published archive.",
    )


class BuildJobFailRequest(BaseModel):
    """Builder reports job failure."""

    error_message: str = Field(
        default="",
        max_length=4096,
        description="Failure reason.",
    )


class BuildLogAppendRequest(BaseModel):
    """Append a chunk of log data to a running build job."""

    data: str = Field(
        ...,
        min_length=1,
        max_length=65536,
        description="Log data chunk (plain text, max 64 KB per append).",
    )


# ── Recipe distribution models ──────────────────────────────────


class RecipeInfo(BaseModel):
    """Public representation of a server-managed recipe."""

    id: int
    name: str
    version: str = ""
    recipe_hash: str = ""
    org_slug: str = ""
    bundle_size: int = 0
    uploaded_by: str = ""
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    updated_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class RecipeListResponse(BaseModel):
    total: int
    recipes: list[RecipeInfo]


# ── Webhook models ─────────────────────────────────────────


class WebhookInfo(BaseModel):
    """Public representation of a webhook."""

    id: int
    url: str
    events: list[str] = Field(default_factory=list)
    org_slug: str = ""
    active: bool = True
    registered_by: str = ""
    created_at: datetime.datetime = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    last_delivery_at: datetime.datetime | None = None
    consecutive_failures: int = 0


class WebhookRegisterRequest(BaseModel):
    """Request body for registering a webhook."""

    url: str = Field(
        ...,
        min_length=1,
        max_length=2048,
        description="HTTPS delivery URL.",
    )
    events: list[str] = Field(
        ...,
        min_length=1,
        description="Events to subscribe to.",
    )
    org_slug: str = Field(
        default="",
        max_length=255,
        description="Organization scope (empty = global).",
    )


class WebhookUpdateRequest(BaseModel):
    """Request body for updating a webhook."""

    url: str | None = Field(None, max_length=2048)
    events: list[str] | None = None
    active: bool | None = None


class WebhookListResponse(BaseModel):
    total: int
    webhooks: list[WebhookInfo]


class TelemetryPayload(BaseModel):
    """Opt-in client telemetry ping (Phase 2 roadmap).

    Strictly anonymous environment facts — no hostname, user, or paths.
    """

    platform: str = Field(default="", max_length=64)
    arch: str = Field(default="", max_length=64)
    python_version: str = Field(default="", max_length=64)
    cvcpkg_version: str = Field(default="", max_length=64)
    ci: bool = False
    tools: dict[str, str] = Field(
        default_factory=dict,
        description="Tool name -> version string (cmake, ninja, compiler, git).",
    )
