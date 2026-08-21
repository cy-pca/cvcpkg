# Analytics and telemetry

The server keeps two separate data streams:

1. **Download analytics** — recorded server-side whenever an archive is
   served. Always on (when the database backend is active); nothing is
   required from the client.
2. **Client telemetry** — anonymous environment pings that a client
   sends only when the user explicitly opts in.

Both require the database backend. The admin `/v1/analytics/*` endpoints and
`POST`/`GET /v1/telemetry` return `503` without it; the public
`GET /v1/downloads/stats` degrades gracefully instead — it returns `200` with
an empty shape (`total: 0`, no daily series).

## What a download event records

Every `GET /v1/download/{filename}` writes one row to
`download_events`:

| Field | Contents |
|---|---|
| `package_name`, `version`, `platform`, `arch` | Resolved from the archive's package row (best-effort filename parse as fallback) |
| `client_ip_hash` | Salted SHA-256 of the client address — see below |
| `user_agent` | Request `User-Agent`, truncated to 255 chars |
| `cvcpkg_version` | From the `X-Cvcpkg-Version` header, or a `cvcpkg/x.y.z` User-Agent prefix |
| `bytes_sent` | Archive size (feeds bandwidth accounting) |
| `downloaded_at` | Server timestamp |

The plain client IP is **never stored**. The address (first
`X-Forwarded-For` entry, else the socket peer) is hashed as
`sha256(salt + ip)` where the salt is the server's token HMAC key —
stable across restarts so aggregates stay consistent, but not
reversible without the server secret. If no HMAC key is configured the
salt falls back to a fixed constant, so configure one in production.
All analytics queries aggregate; there is no per-user reporting.

## Public download stats

`GET /v1/downloads/stats` is public and powers the Downloads chart on
each package page of the web UI:

```sh
curl 'https://cvcpkg.org/v1/downloads/stats?name=zlib&days=30'
```

- `name` (optional) filters to one package; empty means all packages.
- `days` (1–365) selects the history window.
- The response carries `total`, a `daily` series, and a `config` block
  (chart color/fill/height) consumed by the landing-page renderer.

A per-name query for a private org package the caller cannot see
returns the same empty shape as a nonexistent package, so the endpoint
cannot be used as an existence or volume oracle.

Chart defaults are tunable via environment variables:
`CVCPKG_DOWNLOAD_GRAPH_DAYS` (30), `CVCPKG_DOWNLOAD_GRAPH_COLOR`,
`CVCPKG_DOWNLOAD_GRAPH_FILL_COLOR`, `CVCPKG_DOWNLOAD_GRAPH_HEIGHT` (200).

## Admin analytics

These require an **admin** token and return aggregates only. All take
`days` (1–365, default 30):

| Endpoint | Returns |
|---|---|
| `GET /v1/analytics/downloads` | All-time total, filtered total (`name`), top packages (`limit`, 1–100) |
| `GET /v1/analytics/bandwidth` | Total bytes served + zero-filled daily byte series (`name` filter) |
| `GET /v1/analytics/platforms` | Download mix by (platform, arch) and by client version |
| `GET /v1/analytics/trends` | Zero-filled daily download counts (`name` filter) |
| `GET /v1/analytics/telemetry` | Aggregated opt-in telemetry: platform/python/client mix, CI share |

```sh
curl -H "Authorization: Bearer $ADMIN_TOKEN" \
  'https://cvcpkg.org/v1/analytics/downloads?days=30&limit=10'
```

The `/admin` dashboard renders these same aggregates server-side.

## Client telemetry (strictly opt-in)

Nothing is ever sent unless you opt in, in one of two ways:

- set `CVCPKG_TELEMETRY=1` (also `true`/`yes`/`on`) **and**
  `CVCPKG_SERVER_URL` — enables a fire-and-forget ping at the end of
  `cvcpkg install` (best-effort, 5-second timeout, never fails or slows the
  install), sent to the server named by `CVCPKG_SERVER_URL`. The ping is
  silently skipped if that variable is unset — `cvcpkg install` has no
  `--server` flag to fall back on, or
- run `cvcpkg telemetry send` explicitly (consent by invocation).

The payload is anonymous by construction: platform, arch, Python
version, cvcpkg version, a CI flag (from the `CI` env var), and the
first `--version` line of any of `cmake`, `ninja`, `git`, `cc`, `cl`
found on `PATH`. No hostname, no username, no paths.

```sh
# See whether telemetry is enabled and the exact JSON that would be
# sent — prints locally, sends nothing:
cvcpkg telemetry status

# Send one ping now (server from --server or CVCPKG_SERVER_URL):
cvcpkg telemetry send --server https://cvcpkg.org
```

Server side, `POST /v1/telemetry` is unauthenticated but per-IP
rate-limited, rejects pings with more than 16 `tools` entries (`422`, no
truncation), and stores **nothing about the connection** — the telemetry
table has no address column, not even
a hashed one.

## Privacy model

- Client telemetry is **opt-in only**; the default is off and stays off.
- Download analytics stores client addresses only as salted hashes;
  telemetry stores no connection data at all.
- Every reporting endpoint returns aggregates (daily buckets, grouped
  counts) — there is no per-user or per-address query surface.
- `cvcpkg telemetry status` shows the exact payload before you opt in.
