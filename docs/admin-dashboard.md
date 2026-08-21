# Admin Dashboard

`/admin` is a lightweight, server-side-rendered administration UI built into
cvcpkg-server. There is no SPA framework and no client-side data fetching:
every number, table, and chart is rendered into the HTML by the server, so the
analytics endpoints' Bearer-token auth never has to be smuggled into browser
JS. Each page has a REST equivalent — see [api-reference.md](api-reference.md)
for scripting the same operations.

## Signing in

Open `/admin` in a browser. Any dashboard URL shows the login page until you
have a session.

**Admin token form.** Paste an **admin**-role API token; `POST /admin/login`
exchanges it for a short-lived session cookie and the raw token is never
stored. Details of the session:

- Cookie `cvcpkg_admin_session`, HttpOnly, `SameSite=Lax`, scoped to
  `path=/admin` — it is never sent to catalog or download routes.
- Valid for 8 hours; the value is only `<expiry>.<hmac(expiry)>`, signed with
  the server's persisted token HMAC key (the `hmac_key` file in the state
  dir), so sessions survive restarts. If no key is available the server signs
  with a random per-process key instead of a known constant.
- A pre-rotation *grace* secret (see [token-rotation.md](token-rotation.md))
  is rejected at login: the cookie would outlive the grace window, defeating
  rotation as a leak remediation.

**SSO button.** When an OIDC provider is configured (`CVCPKG_OIDC_*`
environment variables), a "Sign in with SSO" button appears above the token
form and starts the authorization-code flow at `/admin/oidc/login`. Users
whose claims map to the `admin` role get the same session cookie; anyone else
gets a 403. The token form always remains available for machine and
break-glass access. See [oidc-identity.md](oidc-identity.md) for provider
setup and role mapping.

"Sign out" (top right) deletes the cookie via `POST /admin/logout`.

## Pages

| Page | Route | REST equivalent |
| --- | --- | --- |
| Overview | `/admin` | `/v1/analytics/*` |
| Packages | `/admin/packages` | yank/unyank/`DELETE` under `/v1/packages` |
| Tokens | `/admin/tokens` | `POST`/`GET`/`DELETE /v1/tokens` |
| Audit | `/admin/audit` | `GET /v1/audit`, `GET /v1/audit/verify` |
| Releases | `/admin/releases` | `GET /v1/packages?release=` |
| Health | `/admin/health` | `GET /v1/admin/stats` |

### Overview

Stat cards (package count, all-time downloads, bandwidth, opt-in telemetry
pings), a downloads-per-day sparkline (inline SVG, no JS), top packages,
platform/arch mix, cvcpkg client versions, and telemetry Python versions.
The analytics window is fixed at 30 days.

### Packages

Filter the catalog by substring — matched against name, version, platform,
arch, build type, link, description, tags, maintainer, license, release tag,
and org slug (typing `linux` matches on platform, `MIT` on license, and so
on). Shows the first 200 matching variants, **including yanked** ones
(tagged `yanked`). Per-variant actions:

- **yank / unyank** — reversible removal from the catalog, scoped to the exact
  variant row (platform/arch/build type/link).
- **delete** — permanent removal, behind a confirm dialog. Like
  `DELETE /v1/packages/{name}/{version}`, delete narrows by **platform and
  link only**, so it can remove sibling arch/build-type variants of the same
  row — prefer yank unless you mean it.

See [api-reference.md](api-reference.md#publishing) for yank semantics
(cvcpkg's yank omits the bundle from the catalog entirely) and yank retention.

### Tokens

Lists all tokens with role, creation date, and active/revoked status. The
create form takes a name and a role (`reader`, `publisher`, `admin`); the raw
`cvctok_…` secret is displayed **once**, in the success banner — copy it
immediately, it is not shown again (only the HMAC hash is stored). Revoking
is behind a confirm dialog and cannot be undone from the UI; to replace a
leaked secret without a new identity, use rotation
([token-rotation.md](token-rotation.md)).

### Audit

The latest 100 entries of the tamper-evident audit log, newest first: time,
action, actor, target, detail. The **Verify chain** button re-hashes the full
chain server-side and reports *intact* or *BROKEN* (equivalent to
`GET /v1/audit/verify`).

### Health

Server stat cards — uptime, database backend (and mirror vs primary mode),
archive storage bytes, audit entries, packages, organizations, total build
jobs, and registered builders with the live-websocket count — plus a builder
fleet table: name, platform/arch, status (`online`/`busy`/`offline`),
current/max jobs, and last heartbeat.

### Releases

Read-only. Lists release tags with their variant counts; the
`(live / untagged)` row is the live channel. Selecting a tag lists its first
200 variants. Release *creation/promotion* tooling (freezing the live channel
into a tag) is the outstanding Phase 3 follow-up — see
[roadmap/CVCPKG-ROADMAP.md](roadmap/CVCPKG-ROADMAP.md).

## What gets audit-logged

All dashboard mutations write to the same audit chain as the REST API:

| Dashboard action | Audit action | Actor recorded |
| --- | --- | --- |
| Login (token or OIDC) | `token_create`, target `admin-ui` | token name / OIDC subject |
| Package yank / unyank / delete | `yank` / `unyank` / `delete`, detail `… via /admin` | `admin-ui` |
| Token create | `token_create`, detail `role=… via /admin` | `admin-ui` |
| Token revoke | `token_revoke` (skipped if nothing was revoked) | `admin-ui` |

Note that package and token mutations record the actor as `admin-ui`, not the
individual admin — correlate with the preceding login entry when you need to
know who was at the keyboard.

## Operational notes

- **Database backend required for management.** Package and token mutations
  return `503` on a server running the plain YAML backend; the Overview page
  degrades to basic stats without analytics.
- **Offline / air-gapped servers render unstyled.** The pages pull Bulma CSS
  from the jsdelivr CDN (`bulma@0.9.4`); with no internet access in the
  browser you get unstyled-but-functional HTML. Everything except the
  delete/revoke confirm dialogs works with JavaScript disabled entirely.
