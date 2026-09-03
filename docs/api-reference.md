# cvcpkg-server API Reference

Base URL: `https://cvcpkg.org` (or your deployment)

Interactive docs: `GET /docs` (Swagger UI) or `GET /redoc` (ReDoc)

## Authentication

All write endpoints require a bearer token:

```
Authorization: Bearer cvctok_...
```

Tokens are created via the admin API or CLI, or through
[self-service registration](#registration--token-requests).  Three roles exist:

| Role | Read | Publish | Admin |
|---|---|---|---|
| `reader` | ✅ | ❌ | ❌ |
| `publisher` | ✅ | ✅ | ❌ |
| `admin` | ✅ | ✅ | ✅ |

Read endpoints are unauthenticated by default.  To require tokens for
reads too, start the server with `cvcpkg-server run --require-auth-reads`;
the flag sets the server environment variable
`CVCPKG_SERVER_REQUIRE_AUTH_READS=1`.  In the docker-compose production
stack the shorter `REQUIRE_AUTH_READS` variable in `.env.production` maps
onto `CVCPKG_SERVER_REQUIRE_AUTH_READS` inside the container.

---

## Health & Monitoring

### `GET /healthz`

Health check endpoint. Returns server status.

**Response** `200 OK`:

```json
{
  "status": "ok",
  "version": "1.3.0",
  "storage_scheme": "file",
  "packages_count": 42,
  "uptime_seconds": 86400.5
}
```

### `GET /metrics`

Prometheus-compatible metrics in text exposition format.

**Response** `200 OK` (`text/plain; version=0.0.4`):

```
cvcpkg_up 1
cvcpkg_uptime_seconds 86400.5
cvcpkg_packages_total 42
cvcpkg_requests_total 1234
cvcpkg_requests_by_method{method="GET"} 1100
cvcpkg_requests_by_method{method="POST"} 130
cvcpkg_requests_by_method{method="DELETE"} 4
cvcpkg_responses{status="2xx"} 1200
cvcpkg_responses{status="4xx"} 30
cvcpkg_responses{status="5xx"} 4
cvcpkg_publishes_total 42
cvcpkg_bytes_uploaded_total 52428800
```

---

## Catalog

### `GET /v1/catalog`

Fetch the full package catalog.

**Query Parameters**:

| Param | Type | Default | Description |
|---|---|---|---|
| `include_yanked` | bool | `false` | Include yanked bundles.  Meant for downstream mirrors, which must tell a deliberate upstream yank apart from a bundle that merely vanished |

**Auth**: Optional (configurable).

**Response** `200 OK`:

```json
{
  "schema_version": 1,
  "revision": 15,
  "bundles": [
    {
      "name": "zlib",
      "version": "1.3.1+cvc.1",
      "platform": "linux",
      "arch": "x86_64",
      "build_type": "release",
      "link": "shared",
      "sha256": "abc123...",
      "size_bytes": 524288,
      "archive_url": "/v1/download/zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.zst",
      "published_at": "2026-05-25T12:00:00+00:00",
      "yanked": false
    }
  ]
}
```

---

## Packages

### `GET /v1/packages`

List packages with filtering and pagination.

**Query Parameters**:

| Param | Type | Default | Description |
|---|---|---|---|
| `name` | string | `""` | Filter by component name |
| `platform` | string | `""` | Filter by platform |
| `arch` | string | `""` | Filter by architecture |
| `build_type` | string | `""` | Filter by build type (`release`/`debug`) |
| `link` | string | `""` | Filter by link mode (`shared`/`static`) |
| `recipe_version` | string | `""` | Filter by recipe version (chain hash); enables exact-match cache lookups |
| `release` | string | `""` | Filter by release tag; `"live"` = no release tag |
| `org` | string | `""` | Filter by organization slug |
| `search` | string | `""` | Full-text search across all attributes |
| `include_yanked` | bool | `false` | Include yanked packages in results |
| `limit` | int | `100` | Page size (1–1000) |
| `offset` | int | `0` | Offset for pagination |

**Response** `200 OK`:

```json
{
  "total": 42,
  "packages": [
    {
      "name": "zlib",
      "version": "1.3.1+cvc.1",
      "platform": "linux",
      "arch": "x86_64",
      "build_type": "release",
      "link": "shared",
      "sha256": "abc123...",
      "size_bytes": 524288,
      "archive_url": "/v1/download/...",
      "published_at": "2026-05-25T12:00:00+00:00",
      "yanked": false,
      "signature": "",
      "key_fingerprint": "",
      "release_tag": "v1.3.0",
      "recipe_version": "a1b2c3d"
    }
  ]
}
```

### `GET /v1/packages/{name}`

List all variants/versions for a specific component.

**Query Parameters**: `org` (filter by organization slug),
`include_yanked` (bool, default `false`).

**Response**: Same format as `GET /v1/packages`.

### `GET /v1/search`

Search the catalog with filters, pagination, and aggregated facet buckets
— this is what backs the landing-page search box.

**Auth**: None (a token, if sent, widens results to private orgs the
caller belongs to).

**Query Parameters**:

| Param | Type | Default | Description |
|---|---|---|---|
| `q` | string | `""` | Full-text substring search across name/tags/etc. |
| `platform` | string | `""` | Filter by platform |
| `arch` | string | `""` | Filter by architecture |
| `build_type` | string | `""` | Filter by build type (`release`/`debug`) |
| `link` | string | `""` | Filter by link mode (`shared`/`static`) |
| `release` | string | `""` | Filter by release tag (`"live"` = unreleased builds) |
| `org` | string | `""` | Filter by organization slug |
| `tag` | string | `""` | Filter by a single tag name |
| `recipe_version` | string | `""` | Filter by recipe version |
| `include_yanked` | bool | `false` | Include yanked packages |
| `limit` | int | `50` | Page size (0–200) |
| `offset` | int | `0` | Offset for pagination |
| `facets` | bool | `true` | Compute facet buckets over the filtered result set |

**Response** `200 OK`: `total`, `package_count` (distinct names),
`total_size_bytes`, `packages` (same shape as `GET /v1/packages`),
`limit`, `offset`, `query`, and `facets` with bucket lists for
`platforms`, `archs`, `build_types`, `links`, `releases`, `orgs`,
`tags`, and `licenses`.

### `GET /v1/deps`

Return forward and reverse dependency maps derived from recipes (the
server's local recipe set plus recipes orgs have pushed with
`cvcpkg recipe push`).  Powers the "Dependencies" / "Used By" blocks on
package pages.

**Auth**: None (unless the server requires auth for reads).

**Response** `200 OK`: `{"forward": {...}, "reverse": {...}, "meta": {...},
"recipe_names": [...]}` — `meta` carries per-recipe description, homepage,
license, and maintainer fields.

### `GET /v1/download/{filename}`

Download a package archive.  `HEAD` is also supported so clients can
check size before downloading.

**Response**: `200 OK` with `application/octet-stream` body. Includes
`Content-Disposition` and `Content-Length` headers.

**Errors**: `404` if the archive does not exist; `410 Gone` if it was
nuked (see [tombstones](#nuked-package-tombstones)).

---

## Recipes (read)

Read-only access to the recipe behind a package, used by the package
pages.  All four endpoints accept an `org` query parameter
(empty = public namespace); private-org recipes require membership.
Distinct from the publisher-facing `/v1/recipes/{name}` store used by
remote builds.

### `GET /v1/recipe/{name}`

Return the raw `recipe.yaml` for a named recipe as `text/yaml`.
`404` if the recipe is unknown.

### `GET /v1/recipe/{name}/files`

List every artifact belonging to a recipe (the yaml, build/test scripts,
patches, media).  Each entry carries `path`, `name`, `size`, `kind`
(`recipe`/`yaml`/`script`/`patch`/`image`/`binary`/`text`), `media_type`,
`shared` (lives under `_common/`), and `too_large`.

### `GET /v1/recipe/{name}/file`

Return the contents of one artifact.

**Query Parameters**: `path` (**required** — path within the recipe set),
`org`.

**Errors**: `404` unknown file, `413` file too large to display inline.

### `GET /v1/recipe/{name}/archive`

Download the recipe and its scripts as one archive, including the shared
`_common/` helpers.  Extracting into a `recipes/` directory yields a
well-formed recipe usable with `cvcpkg build <name> --recipes-dir <dir>`.

**Query Parameters**: `format` (`tar.gz` default, or `zip`), `org`.

---

## Publishing

### `POST /v1/publish`

Upload and publish a package bundle.

**Auth**: `publisher` or `admin`.

**Rate Limited**: Yes (configurable via `CVCPKG_RATE_LIMIT_RPM`).

**Size Limited**: Yes — `cvcpkg-server run --max-upload-bytes` / `CVCPKG_MAX_UPLOAD_BYTES`, default 4 GiB.  Accepts a byte count or a human size (`8GB`, `512MB`); units are binary.

**Query Parameters**:

| Param | Type | Required | Description |
|---|---|---|---|
| `name` | string | ✅ | Component name |
| `version` | string | ✅ | Component version |
| `platform` | string | ❌ | Target platform (e.g. `linux`) |
| `arch` | string | ❌ | Target architecture (e.g. `x86_64`) |
| `build_type` | string | ❌ | `release` or `debug` (default: `release`) |
| `link` | string | ❌ | `shared` or `static` (default: `shared`) |
| `signature` | string | ❌ | Base64url Ed25519 signature |
| `key_fingerprint` | string | ❌ | SHA-256 fingerprint of signing key |
| `release_tag` | string | ❌ | Release tag (e.g. `v1.3.0`) |
| `recipe_version` | string | ❌ | Recipe revision hash |

**Body**: `multipart/form-data` with a `file` field containing the `.tar.zst` archive.

**Response** `200 OK`:

```json
{
  "name": "zlib",
  "version": "1.3.1+cvc.1",
  "sha256": "abc123...",
  "archive_url": "/v1/download/zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.zst",
  "message": "published"
}
```

**Errors**:

| Code | Meaning |
|---|---|
| `401` | Missing or invalid token |
| `403` | Insufficient role |
| `409` | Duplicate package (same name+version+platform+arch+build_type+link) |
| `413` | Upload exceeds size limit |
| `429` | Rate limit exceeded |

### Chunked uploads

For large archives, or where a single `POST /v1/publish` is impractical,
publish in resumable chunks.  All five endpoints require `publisher` or
`admin`.  A session belongs to the token that created it — writes
(`PATCH`, `.../complete`) and cancellation (`DELETE`) by other actors get
`403`; the status endpoint (`GET`) only requires a publisher/admin token
and does not check ownership.

#### `POST /v1/upload/init`

Initialise a chunked upload session.  Takes the same query parameters as
`POST /v1/publish` (including the metadata params `description`,
`homepage`, `license`, `maintainer`, `tags`, `required_deps`,
`provides`, and `org`, which publish also accepts) plus `total_size`
(bytes, `0` = unknown).  Rate limited; duplicates are rejected with
`409` up front.

**Response** `201 Created`:

```json
{
  "upload_id": "…",
  "chunk_size": 8388608,
  "max_size": 4294967296,
  "expires_in": 3600
}
```

Chunk size defaults to 8 MiB (`CVCPKG_CHUNK_SIZE`); idle sessions expire
after `expires_in` seconds (default 3600, `CVCPKG_UPLOAD_SESSION_TTL`).

#### `PATCH /v1/upload/{upload_id}`

Append a chunk.  Body is the raw chunk bytes
(`Content-Type: application/octet-stream`).  Include
`Content-Range: bytes {start}-{end}/{total}` for resume safety — the
server returns `409` with the current offset if `start` does not match
`bytes_received`.  Returns `{upload_id, bytes_received, total_size}`.

#### `GET /v1/upload/{upload_id}`

Status of an in-progress upload: `bytes_received`, `total_size`, `name`,
`version`.

#### `POST /v1/upload/{upload_id}/complete`

Finalise: verify integrity and register the package.  Optional
`expected_sha256` query parameter — on mismatch the session is discarded
with `422`.  Returns the same response as `POST /v1/publish`; `409` if a
concurrent publish raced this session to the same coordinates.

#### `DELETE /v1/upload/{upload_id}`

Cancel and discard an in-progress session.  Returns `204`.

### `POST /v1/packages/{name}/{version}/yank`

Mark a package as yanked. Auth: `publisher` or `admin` — a publisher may only
yank packages it published, or that belong to an org it is a member of.

| Query param | Effect |
| --- | --- |
| `platform` | Only yank bundles for this platform |
| `arch` | Only yank bundles for this arch |
| `link` | Only yank bundles for this link mode (`shared`/`static`) |
| `build_type` | Only yank bundles for this build type (`release`/`debug`) |

**Omitting a scope param matches every value of it**, so a call with no params
yanks *every* variant of that version. A call that matches nothing returns
`200` with `{"count": 0}` rather than an error — check `count`.

```json
{ "message": "yanked readline==8.3+cvc.1 [platform=linux]", "count": 1 }
```

Yanking hides bundles from the catalog and from dependency resolution. It does
not delete the archive, and it is reversible. Note it is stronger than cargo's
yank: the catalog **omits** yanked bundles rather than flagging them, so a pin
to a yanked version stops resolving too — that install falls back to building
from source, or fails.

Prefer the CLI, which previews the affected bundles, warns when a yank would
leave a variant with no active bundle, and refuses a no-op instead of reporting
`count: 0`:

```console
$ cvcpkg yank readline 8.3+cvc.1 --platform linux --arch x86_64 \
      --config release --link shared
```

### `POST /v1/packages/{name}/{version}/unyank`

Remove yank status. Auth: `admin` only — unlike yank, a publisher cannot
un-retire even its own package. Takes the same scope query params.

```console
$ cvcpkg unyank readline 8.3+cvc.1 --platform linux --arch x86_64
```

To find a yanked bundle to restore (yanked bundles are hidden from the normal
catalog), reveal them with search:

```console
$ cvcpkg search readline --yanked-only     # or --include-yanked to show both
```

### `POST /v1/packages/{name}/{version}/nuke`

**Irreversibly** delete a yanked bundle's catalog row **and its archive bytes**.
Auth: `admin` only. Takes the same scope query params as yank.

Unlike yank (reversible; the archive stays) this destroys the archive; use it
to reclaim storage before yank retention elapses. The bundle **must already be
yanked** — nuking a live bundle returns `409` (yank it first), so nuke only
ever accelerates retention. `404` if nothing matches.

```console
$ cvcpkg nuke readline 8.3+cvc.1 --platform linux --arch x86_64 \
      --config release --link shared
```

The CLI has no `--yes`: you confirm by typing `name==version` (or
`--confirm name==version` for automation).

### Yank retention

A yanked bundle is permanently purged (row **and** archive) once it has been
yanked longer than `CVCPKG_YANK_RETENTION_DAYS` (default **0 = disabled**; the
recommended value is `365`). Rows yanked before the `yanked_at` column existed
(`yanked_at IS NULL`) and tagged releases (`release_tag != ""`) are never
auto-purged. A mirror never purges — it may hold the last copy of a bundle its
upstream retired.

Run it on demand, admin only (dry-run by default):

```
POST /v1/admin/gc/yanked?older_than_days=365&dry_run=true
```

Retention purges are audited under the `nuke` action with actor `retention-gc`.

### Nuked-package tombstones

A nuked bundle's catalog row is hard-deleted (freeing the slot for republish),
so a lightweight **tombstone** records that it existed and why it went away.
Every nuke — manual or retention — writes one, carrying `reason` (`manual` = an
admin's `cvcpkg nuke`, `retention` = fell off the schedule), `nuked_by`,
`nuked_at`, and forensic context (sha256, sizes, the original `published_at` and
`yanked_at`).

`GET /v1/packages/{name}/tombstones` — records for `name`, newest first;
narrow with the same scope query params. Private-org tombstones are visible only
to a member or admin.

Because of tombstones, **downloading a nuked archive returns `410 Gone`** with
the reason and date (e.g. `readline==8.3+cvc.1 was nuked (retention) on
2026-07-17`) rather than a bare `404` — a consumer whose lockfile pinned it
learns it was retired, not that it never existed. A genuinely unknown archive is
still `404`.

### `DELETE /v1/packages/{name}/{version}`

Delete a package's catalog row. Auth: `admin` only. **Legacy** — it accepts only
`platform`/`link` scope (not `arch`/`build_type`) and does not delete the
archive bytes. Prefer `nuke`, which has full scope and removes the archive.

---

## Build Cache

The server doubles as a build cache: bundles published without a
`release_tag` are cache entries, keyed by recipe chain hash.

### `GET /v1/cache/status`

Cache probe: is a bundle for this exact recipe state already published?

**Auth**: None; a private org requires a token with membership.

**Query Parameters**: `name`, `chain_hash`, `platform` (**required**);
`arch`, `build_type`, `link`, `org` (optional narrowing).

**Response** `200 OK`: `{"hit": false}` or `hit: true` plus the bundle's
coordinates, `archive_url`, `sha256`, and `size_bytes`.

### `GET /v1/cache`

List non-release cache entries (packages with an empty `release_tag`).
Auth: `publisher` or `admin`.

**Query Parameters**: `name`, `platform`, `arch`, `limit` (1–1000,
default 100), `offset`.

### `DELETE /v1/cache`

Bulk-purge non-release cache entries. Auth: `admin`.

**Query Parameters**: `older_than` — only delete entries older than this
duration (e.g. `14d`).  **Without it, every non-release package is
removed.**  Returns `deleted_count` and the deleted rows.

### `GET /v1/cache/stats`

Storage statistics: totals, package counts, and a per-org breakdown.
Auth: `publisher` or `admin` — non-admins see only orgs they belong to
in the breakdown.

### `POST /v1/cache/gc`

Run garbage collection on cached packages. Auth: `admin`.

**Body**: JSON with one or more of `max_age_seconds` (delete non-release
packages older than this), `max_storage_bytes` (evict oldest to fit
under the cap), `valid_chain_hashes` (list of current chain hashes —
entries whose `recipe_version` is not in the set are stale and removed).
`422` if none are given.

---

## Token Management

### `POST /v1/tokens`

Create a new API token. Auth: `admin`.

**Body**:

```json
{
  "name": "ci-publisher",
  "role": "publisher",
  "expires_in_days": 365
}
```

**Response** `200 OK`:

```json
{
  "name": "ci-publisher",
  "role": "publisher",
  "token": "cvctok_...",
  "expires_at": "2027-05-25T00:00:00+00:00"
}
```

> **The token value is shown only once.** Store it securely.

### `GET /v1/tokens`

List all tokens (without secrets). Auth: `admin`.

### `DELETE /v1/tokens/{name}`

Revoke a token by name. Auth: `admin`.

### `POST /v1/tokens/{name}/rotate`

Rotate a token's secret **in place**: the token keeps its name, role,
expiry, and org memberships — only the secret changes. Auth: `admin`
(any token) or the token itself (self-rotation).

**Body** (optional):

```json
{
  "grace_minutes": 60
}
```

`grace_minutes` (0–10080, default 0) keeps the pre-rotation secret
working while stored copies (CI secrets, config files) are updated;
`0` kills it immediately. Revoking the token ends both secrets at once.

**Response** `200 OK`:

```json
{
  "name": "ci-publisher",
  "role": "publisher",
  "token": "cvctok_...",
  "expires_at": "2027-05-25T00:00:00+00:00",
  "previous_valid_until": "2026-07-16T13:00:00+00:00"
}
```

> **The new token value is shown only once.** Store it securely.

See [Token Rotation](token-rotation.md) for the full workflow, security
model, and CI examples.

---

## Registration & Token Requests

Self-service signup.  The policy is set at server start with
`cvcpkg-server run --registration-mode open|admin-gated` (or
`CVCPKG_REGISTRATION_MODE`; default `open`).

### `POST /v1/register`

Register for a token. No auth; rate limited.

**Body**:

```json
{
  "name": "alice",
  "email": "alice@example.com",
  "role": "publisher",
  "description": "optional",
  "metadata": ""
}
```

`name` (a valid C identifier) and `email` are required.  In **open**
mode the token is created immediately and returned once — the requested
role is ignored; open self-registration is always `reader`.  In
**admin-gated** mode a pending request is recorded instead and the
response carries a `request_id` (requires the database backend).
`409` if the username is taken.

### `GET /v1/token-requests`

List registration requests. Auth: `admin`.  Optional `status` filter
(`pending`/`approved`/`denied`).

### `POST /v1/token-requests/{request_id}/approve`

Approve a pending request. Auth: `admin`.  Creates the token with the
role the requester asked for and returns the raw token value in the
response — the admin must relay it to the requester.  `409` if already
resolved.

### `POST /v1/token-requests/{request_id}/deny`

Deny a pending request. Auth: `admin`.  `409` if already resolved.

---

## Audit Trail

### `GET /v1/audit`

Query the tamper-evident audit log. Auth: `admin`.

**Query Parameters**:

| Param | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `100` | Page size (1–1000) |
| `offset` | int | `0` | Offset |
| `action` | string | — | Filter by action (`publish`, `yank`, `delete`, etc.) |
| `target` | string | `""` | Filter by target (e.g. `zlib==1.3.1`) |

### `GET /v1/audit/verify`

Verify the integrity of the audit hash chain. Auth: `admin`.

**Response** `200 OK`:

```json
{
  "ok": true,
  "message": "chain intact (15 entries)"
}
```

If tampering is detected: `409 Conflict` with `"ok": false`.

---

## Organizations

### `POST /v1/orgs`

Create an organization. Auth: `publisher` or `admin`.

**Body**:

```json
{
  "slug": "my-team",
  "display_name": "My Team",
  "description": "optional description",
  "homepage": "https://example.com",
  "is_private": false
}
```

**Response** `201 Created`:

```json
{
  "slug": "my-team",
  "display_name": "My Team",
  "description": "optional description",
  "logo_url": "",
  "homepage": "https://example.com",
  "is_private": false,
  "storage_limit_bytes": 10737418240,
  "storage_used_bytes": 0,
  "created_at": "2026-05-25T12:00:00+00:00",
  "created_by": "alice"
}
```

The creator is automatically added as an **owner**.

**Errors**: `409` duplicate slug, `422` invalid slug format.

### `GET /v1/orgs`

List public organizations. No auth required.

**Query Parameters**:

| Param | Type | Default | Description |
|---|---|---|---|
| `limit` | int | `100` | Page size (1–1000) |
| `offset` | int | `0` | Offset for pagination |

**Response** `200 OK`:

```json
{
  "total": 3,
  "organizations": [ OrgInfo, ... ]
}
```

Private organizations are excluded unless the caller is a member or admin.

### `GET /v1/orgs/{slug}`

Get organization details, members, and packages. No auth required for
public orgs; private orgs return `404` to non-members.

**Response** `200 OK`:

```json
{
  "org": { OrgInfo },
  "members": [
    {
      "token_name": "alice",
      "role": "owner",
      "added_at": "2026-05-25T12:00:00+00:00"
    }
  ],
  "packages": [ PackageInfo, ... ]
}
```

### `PATCH /v1/orgs/{slug}`

Update organization settings. Auth: org owner or `admin`.

**Body** (all fields optional):

```json
{
  "display_name": "New Name",
  "description": "updated",
  "homepage": "https://example.com",
  "is_private": true,
  "storage_limit_bytes": 21474836480
}
```

Only admins can change `storage_limit_bytes`.

**Response** `200 OK`: Updated `OrgInfo`.

### `POST /v1/orgs/{slug}/logo`

Upload an organization logo. Auth: org owner or `admin`.

**Body**: `multipart/form-data` with a `file` field.

Accepted types: `image/png`, `image/jpeg`, `image/svg+xml`, `image/webp`.
Max size: 512 KB.

**Response** `200 OK`:

```json
{
  "message": "logo uploaded",
  "logo_url": "/v1/orgs/my-team/logo"
}
```

### `GET /v1/orgs/{slug}/logo`

Serve the organization logo. No auth required.

**Response**: `200 OK` with the image content and appropriate `Content-Type`.
`404` if no logo has been uploaded.

### `POST /v1/orgs/{slug}/members`

Add a member to the organization. Auth: org owner or `admin`.

**Query Parameters**:

| Param | Type | Default | Description |
|---|---|---|---|
| `token_name` | string | — | **(required)** Token name of the user to add |
| `role` | string | `member` | `member` or `owner` |

**Response** `200 OK`:

```json
{
  "message": "added 'alice' to 'my-team' as member"
}
```

**Errors**: `403` not an owner, `404` org or token not found, `409` already
a member.

### `DELETE /v1/orgs/{slug}/members/{token_name}`

Remove a member from the organization. Auth: org owner or `admin`.

**Response** `200 OK`:

```json
{
  "message": "removed 'alice' from 'my-team'"
}
```

**Errors**: `403` not an owner, `404` member not found.

### Publishing to an Organization

Pass `org` as a query parameter when publishing:

```
POST /v1/publish?name=my-lib&version=1.0.0&org=my-team
```

The server validates org membership and storage limits before accepting the
upload. Returns `403` if the publisher is not a member, or `413` if the
upload would exceed the org's storage limit.

---

## Webhooks

Event notifications delivered as HTTP POSTs.  All management endpoints
are `admin`-only and require the database backend.

Deliveries are JSON bodies of the form
`{"event": …, "timestamp": …, "data": {…}}` with headers
`X-CvcPkg-Event`, `X-CvcPkg-Delivery` (unique id), and
`X-CvcPkg-Signature: sha256=<hex>` — an HMAC-SHA256 of the body keyed by
the webhook's secret.  Failed deliveries are retried with backoff.
Events include `package.published`, the `build.*` lifecycle
(`build.started`, `build.completed`, `build.succeeded`, `build.failed`,
`build.cancelled`, `build.timed_out`, `build.unschedulable`), and
`builder.online` / `builder.offline`.

### `POST /v1/webhooks`

Register a webhook.

**Body**: `url` (**required** — must be an http(s) URL resolving to a
public address), `events` (**required**, non-empty list), `org_slug`
(optional scope; empty = global).

### `GET /v1/webhooks`

List webhooks.  Optional `org_slug` filter, `limit` (1–1000), `offset`.

### `GET /v1/webhooks/{webhook_id}`

Get one webhook's details.

### `PATCH /v1/webhooks/{webhook_id}`

Update `url`, `events`, and/or `active`.

### `DELETE /v1/webhooks/{webhook_id}`

Delete a webhook.

### `POST /v1/webhooks/{webhook_id}/test`

Send a signed `webhook.test` payload to the endpoint now.  Returns the
delivery's status code, or `502` if delivery failed.

---

## Mirrors

Registry of downstream mirror servers.  See
[clusters-and-federation.md](clusters-and-federation.md) for how
mirroring works.

### `POST /v1/mirrors/register`

Register a mirror with the primary. Auth: `admin`.  Requires the
database backend; unavailable on a server that is itself in mirror mode.
Re-registering an existing URL clears a previous rejection and resets
health state.

**Body**: `url` (**required**, `http(s)://`), `display_name`, `contact`.

### `GET /v1/mirrors`

List healthy mirrors for client failover.  No auth required (unless the
server requires auth for reads).

### `GET /v1/mirrors/all`

List all mirrors including rejected and unhealthy ones. Auth: `admin`.

### `POST /v1/mirrors/reject`

Reject a mirror (query param `url`), removing it from the public list
while keeping it in the database for audit. Auth: `admin`.

### `DELETE /v1/mirrors`

Permanently remove a mirror (query param `url`). Auth: `admin`.

### `GET /v1/mirror/download/{filename}`

Only on a server running in mirror mode: proxy a package download from
the upstream, caching the archive locally for subsequent requests.

---

## Feed

### `GET /v1/feed.xml`

RSS 2.0 feed of the latest published packages.  Optional `limit`
(1–200, default 50).  No auth required (unless the server requires auth
for reads).

---

## Analytics & Telemetry

Aggregate download, bandwidth, and client-environment statistics.  This
is a summary — see
[analytics-and-telemetry.md](analytics-and-telemetry.md) for data
collection, privacy model, and CLI usage.

### `GET /v1/downloads/stats`

Daily download counts for charting (used by package pages).  No auth
required.  Params: `name` (empty = all packages), `days` (1–365,
default 30).

### `GET /v1/analytics/downloads`

Download totals and top packages. Auth: `admin`.  Params: `name`,
`days`, `limit`.

### `GET /v1/analytics/bandwidth`

Total bytes served plus a daily series. Auth: `admin`.  Params: `name`,
`days`.

### `GET /v1/analytics/platforms`

Download distribution by platform/arch and client version. Auth:
`admin`.  Param: `days`.

### `GET /v1/analytics/trends`

Zero-filled daily download buckets. Auth: `admin`.  Params: `name`,
`days`.

### `POST /v1/telemetry`

Accept an opt-in, anonymous client telemetry ping (sent only when
`CVCPKG_TELEMETRY=1` or via `cvcpkg telemetry send`).  No auth; rate
limited.  Returns `204`.

### `GET /v1/analytics/telemetry`

Aggregated telemetry summary: platform/python/client mix and CI share.
Auth: `admin`.  Param: `days`.

---

## Admin

### `GET /v1/admin/settings`

Current server-wide settings: `global_cache_storage_limit_bytes`,
`org_storage_limit_bytes`, `max_upload_bytes`, `rate_limit_rpm`.
Auth: `admin`.

### `PATCH /v1/admin/settings`

Update settings at runtime. Auth: `admin`.

**Body**: JSON with one or both of `global_cache_storage_limit_bytes`,
`org_storage_limit_bytes`.  Changes take effect immediately but are
**not** persisted across restarts — use environment variables for
permanent configuration.

### `POST /v1/admin/backup`

Trigger a database backup. Auth: `admin`.  Writes a timestamped
snapshot under `<state_dir>/backups` and returns its path and size; the
strategy depends on the backend (sqlite `VACUUM INTO`, `pg_dump`, or
`mysqldump`).  Requires the database backend.

### `GET /admin/oidc/login` / `GET /admin/oidc/callback`

Browser endpoints for the admin dashboard's OIDC single sign-on
(authorization-code flow with PKCE).  `404` unless an OIDC provider is
configured; only identities that map to the `admin` role get a
dashboard session.  See [oidc-identity.md](oidc-identity.md).

---

## Error Format

All errors return JSON:

```json
{
  "detail": "error description"
}
```

Standard HTTP status codes are used:

| Code | Meaning |
|---|---|
| `200` | Success |
| `401` | Authentication required |
| `403` | Forbidden (role too low) |
| `404` | Resource not found |
| `409` | Conflict (duplicate / tampered) |
| `413` | Payload too large |
| `429` | Rate limited |
| `500` | Internal server error |
