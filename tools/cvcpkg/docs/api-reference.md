# cvcpkg-server API Reference

Base URL: `https://pkg.tx.wtf` (or your deployment)

Interactive docs: `GET /docs` (Swagger UI) or `GET /redoc` (ReDoc)

## Authentication

All write endpoints require a bearer token:

```
Authorization: Bearer cvctok_...
```

Tokens are created via the admin API or CLI.  Three roles exist:

| Role | Read | Publish | Admin |
|---|---|---|---|
| `reader` | ✅ | ❌ | ❌ |
| `publisher` | ✅ | ✅ | ❌ |
| `admin` | ✅ | ✅ | ✅ |

Read endpoints are unauthenticated by default but can require tokens
when `REQUIRE_AUTH_READS=1` is set.

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

**Query Parameters**: None.

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
| `release` | string | `""` | Filter by release tag; `"live"` = no release tag |
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

**Response**: Same format as `GET /v1/packages`.

### `GET /v1/download/{filename}`

Download a package archive.

**Response**: `200 OK` with `application/octet-stream` body. Includes
`Content-Disposition` and `Content-Length` headers.

**Error**: `404` if the archive does not exist.

---

## Publishing

### `POST /v1/publish`

Upload and publish a package bundle.

**Auth**: `publisher` or `admin`.

**Rate Limited**: Yes (configurable via `CVCPKG_RATE_LIMIT_RPM`).

**Size Limited**: Yes (configurable via `CVCPKG_MAX_UPLOAD_BYTES`, default 512 MiB).

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

### `POST /v1/packages/{name}/{version}/yank`

Mark a package as yanked. Auth: `publisher` or `admin`.

### `POST /v1/packages/{name}/{version}/unyank`

Remove yank status. Auth: `admin` only.

### `DELETE /v1/packages/{name}/{version}`

Permanently delete a package. Auth: `admin` only.

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
