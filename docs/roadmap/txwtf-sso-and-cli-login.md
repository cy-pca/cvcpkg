# cvcpkg.org ↔ tx.wtf SSO — Final Implementation Plan

Wiring cvcpkg.org logins to the tx.wtf federated identity network, so interactive
users stop hand-cutting API tokens on the command line. Machine tokens are unaffected.

Line numbers refer to `origin/master` at `d69df4de` (2026-09-08).

---

## 0. Decisions taken (2026-09-10)

These were open forks in the design review; they are now settled and the stages below
are written against them.

| Fork | Decision |
|---|---|
| Role when an authenticated tx.wtf user matches no group map | **`reader`.** `CVCPKG_OIDC_DEFAULT_ROLE=reader`. Any tx.wtf account gets a named, non-anonymous cvcpkg identity that an org owner can add to a private org by handle. |
| May a CLI session hold `admin`? | **Yes.** No `publisher` ceiling. `CVCPKG_CLI_MAX_ROLE` still exists and defaults to `admin`, so a deployment can lower it, but cvcpkg.org will not. Because the blast radius of a phished pairing code is now server takeover rather than publish, the approval-screen mitigations in §9 are **mandatory, not optional**: never skip the confirmation screen, default the role selector to the lowest the user is entitled to, and require an explicit, deliberate choice to elevate a session to `admin`. |
| Testing outside production | **Stub IdP.** Tests run against a local fake OIDC provider; the real tx.wtf leg is exercised only on cvcpkg.org. No dev TLS, no dev hostname, no second registered client. Accepted consequence: the first real tx.wtf round-trip happens in production, so Stage 1's ship gate (§3) is the moment of truth and must be run deliberately. |
| tx.wtf client registration + groups | **Code first.** Build and test everything against the stub IdP. The `register-oidc-client` invocation and the group-creation calls (§2.1, §2.2) happen as a separate, later step once the code is ready to light up. |


---

## 1. The decision

**Build the broker: cvcpkg.org becomes a small first-party authorization server; tx.wtf authenticates the human.** The credential handed to the CLI is a new **opaque `cvcses_` session**, not a `cvctok_`.

### Resolving the judge disagreement

The UX and feasibility judges both ranked **Pairing Broker** first (SSH-only boxes; zero dependencies). The security judge ranked it **last** (code transcription is its *only* path) and put the **full-AS Broker** first (short credential lifetime, real deprovisioning). Both are right about the thing they measured, and the disagreement dissolves once you notice the two positions are about *different layers*:

- Pairing vs. loopback is a **grant type**. Once cvcpkg.org is the authorization server, both are branches on one `/v1/auth/token` endpoint sharing one identity resolution. Ship **both**: loopback is the desktop default (kills the "phishing is the only path" objection), pairing is the headless default (kills the "43 characters in 60 seconds" objection). Cost of the second grant is ~200 LOC, not a second architecture.
- Credential *lifetime* is orthogonal to grant type. Take the full-AS design's answer (12h session + rotating refresh + 7-day hard reauth horizon), because a 30-day admin-capable static bearer in `credentials.yaml` is the single thing the security lens says not to do.

### The catch that decides the identity model

The feasibility judge's grep is correct and I re-verified it on master:

```
$ grep -n "OrgMemberRow.token_name" src/cvcpkg/server/db_stores.py
385, 914, 1034, 1179, 2167, 2196, 2248, 2282, 2323, 2340, 3559   # 11 sites
$ grep -c "actor\.name" src/cvcpkg/server/app.py
105
```

Six of those eleven are **private-org visibility filters** (`search_users`, `get_bundles`, `get_search_facets`, `get_catalog_dict`, `list_orgs`, `list_jobs`), not the three `DbOrgStore` methods. So:

- **Pairing Broker's** `token_name IN (name, principal)` on three methods would let a freshly-paired device join an org and then 404 on its private catalog. Wrong.
- **Full-AS Broker's** `actor.name → actor.principal_handle` sweep is 105 sites, not ~13, on the riskiest change in its plan. Unaffordable.
- **Broker Login's** "one token per human, name == identity" avoids both — but `uq_tokens_active_name` (`db.py:323-336`, verified) permits **one *active* token per name**, so a second login revokes the first machine's credential. Unshippable for a fleet.

**Resolution — the graft:** a session carries the principal **as `TokenRecord.name`**. `DbSessionStore.verify()` returns a `TokenRecord` whose `.name` is `"joe"`. Then:

- Zero changes to those 11 predicates, zero to the 105 `actor.name` sites, zero backfill migration.
- `org_members.token_name = "joe"` is exactly what an org owner types today with `cvcpkg org add-member cvc joe`.
- Multi-device works: three live sessions, all `principal="joe"`. `sessions` has no name-unique index; `uq_tokens_active_name` is on `tokens` and is untouched.
- Per-device revocation is one session row; per-human revocation is a cascade on `principals.id`.

The **entire seam** is the ten inline `_db_tokens.verify(raw) if _use_db else state.tokens.verify(raw)` expressions in `app.py` (lines 676/678, 706/708, 726, 735/737, 4944, 5008/5010, 5048/5050, 5098/5100, 5124, 6037/6040 — enumerated exhaustively above). Replace them with one `_verify_credential(raw)` that dispatches on prefix.

### Grafts from the runners-up

| From | Idea | Why |
|---|---|---|
| Broker Login | Reserved-name guard **inside `DbTokenStore.create()`** | The principal name is now the org-membership key. `create()` (`db_stores.py:107-155`) is the one chokepoint all four mint paths cross — including `/admin/tokens/create`, which applies only `name.strip()` (`app.py:6302`, verified) and skips `_C_IDENTIFIER_RE`. |
| Broker Login | Loopback matcher refuses `localhost`, literal IPs only (RFC 8252 §8.3) | `localhost` is DNS-resolvable and steerable. |
| Pairing Broker | Client-generated pairing verifier (`sha256` registered at start, plaintext only at collection) | Strictly stronger than RFC 8628: a `pairing_id` leaked into a proxy log cannot collect. |
| Pairing Broker | `code_attempts` in **Postgres**, not `_rate_buckets` | `_check_rate_limit` is per-worker and resets on restart. `--workers 1` is a `CMD` flag, not a guarantee. |
| Pairing Broker | `derive_key(hmac_key, purpose)` | One 32-byte file today signs API tokens, admin cookies and OIDC txn cookies with one message prefix between them. |
| Full-AS | `WWW-Authenticate: Bearer error="invalid_token"` + exit code 77 | Makes transparent client-side refresh possible. |
| Full-AS | Single-use 60s stream ticket | Retires the raw bearer in the EventSource URL (`landing.py:3022`). |
| Full-AS | Refuse to boot if tx.wtf's `user` group appears in any role map | Every self-registered account holds it. Machine-enforced, not prose. |

### On the no-JWT roadmap stance

`CVCPKG-ROADMAP.md:1628-1634` contractually refuses a JWT/JWKS dependency. **Keep it, and do not put a maintainer policy decision on the critical path.** The real defect — the nonce is minted (`app.py:6104`) and never read (`app.py:6144-6205`, verified) — is closed in Stage 2 by base64-decoding the `id_token` *payload* and comparing `nonce`, with **no signature check and no dependency**. Be honest in the docstring about what that does and does not prove: it is defence-in-depth against a mismatched-token mix-up, not a signature check. Full RS256/JWKS verification lands in Stage 4 behind `CVCPKG_OIDC_VERIFY_ID_TOKEN=1` and an optional `[server]`-extra `PyJWT`, as a separate PR with its own roadmap amendment.

txwtf's own guidance (`docs/OIDC.md:496-500`) says re-read `/oauth/userinfo` rather than trust a cached ID token — which is what we already do on every login.

---

## 2. Outside the repo

### 2.1 Register the OIDC client at tx.wtf

`register-oidc-client` writes straight to the DB (`txwtf/__main__.py:301-392`, verified: `--scopes` is plural, default `"openid profile email groups"`; `--redirect-uri` is `multiple=True`; `--public` is a flag).

```bash
txwtf register-oidc-client \
  --db-url "$TXWTF_DB_URL" \
  --name "cvcpkg.org" \
  --redirect-uri https://cvcpkg.org/auth/oidc/callback \
  --redirect-uri https://cvcpkg.org/admin/oidc/callback \
  --scopes "openid profile email groups"
```

- **Confidential** — no `--public`. cvcpkg.org keeps a secret; the CLI never sees it.
- **Two URIs.** Only the first is used by the new code; the second keeps a stale `CVCPKG_OIDC_REDIRECT_URL` working. Matching is a bare Python `in` over a newline-split column (`txwtf/api/oidc.py:589`, `txwtf/core/oidc_flow.py:380-382` — verified), so both must be byte-exact.
- **No loopback URI is ever registered upstream.** The CLI's `http://127.0.0.1:<port>/callback` is validated by *cvcpkg*, which is why the missing RFC 8252 §7.3 port relaxation upstream (`_LOOPBACK_HOSTS` is referenced only at `oidc_flow.py:282`, registration-time scheme validation) is a non-issue.
- **No `offline_access`.** We do not custody upstream refresh tokens.
- Secret is printed once. Capture straight into `.env.production`.

### 2.2 Groups on tx.wtf

Create and populate `cvcpkg-admin`, `cvcpkg-publisher`, `cvcpkg-reader` via `POST /group/`. Confirm the client's `allowed_scopes` contains `groups` — the authorize-time subset check (`txwtf/api/oidc.py:653-666`, verified) drops the scope otherwise and the symptom is "everyone is refused."

**Never map `user`.** Every self-registered tx.wtf account holds it. Stage 1 adds a startup assertion that refuses to boot if `user` appears in any of `CVCPKG_OIDC_{ADMIN,PUBLISHER,READER}_GROUPS`.

### 2.3 Rate-limit exemption — pre-launch blocker

`POST /oauth/token` is tier `auth_strict` keyed on requester IP (`txwtf/api/ratelimit.py:89`, verified). Brokering means that IP is always cvcpkg-00's egress address, so all cvcpkg logins share one budget.

Add cvcpkg-00's egress IP to **`TXWTF_RATE_LIMIT_EXEMPT_IPS`** (`txwtf/core/ratelimit.py:197`). **Start this on day one** — it is someone else's turnaround.

### 2.4 Reverse proxy on the tx VM

TLS terminates on a different host (38.57.161.23) from the backend (10.10.10.134:8420). The documented vhost has `ProxyPreserveHost On` and **no** `X-Forwarded-Proto`. Add:

```apache
RequestHeader set X-Forwarded-Proto "https"
```

and run uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy ip>` (`Dockerfile.production:77-81` CMD). Without this, `Secure` cookies and scheme detection misbehave silently.

### 2.5 Secrets on cvcpkg-00

Append to `/home/tfx/libcvc-deps/.env.production` (host-local, excluded from the deploy rsync at `deploy-prod.yml:133`):

```
CVCPKG_OIDC_ISSUER=https://tx.wtf
CVCPKG_OIDC_CLIENT_ID=<from 2.1>
CVCPKG_OIDC_CLIENT_SECRET=<from 2.1>
CVCPKG_OIDC_REDIRECT_URL=https://cvcpkg.org/auth/oidc/callback
CVCPKG_OIDC_SCOPES=openid profile email groups
CVCPKG_OIDC_GROUPS_CLAIM=groups
CVCPKG_OIDC_ADMIN_GROUPS=cvcpkg-admin
CVCPKG_OIDC_PUBLISHER_GROUPS=cvcpkg-publisher
CVCPKG_OIDC_READER_GROUPS=cvcpkg-reader
CVCPKG_OIDC_ADMIN_EMAILS=
CVCPKG_OIDC_DEFAULT_ROLE=
CVCPKG_PUBLIC_URL=https://cvcpkg.org
CVCPKG_COOKIE_SECURE=1
```

**This alone does nothing** until the compose change ships: `--env-file` feeds compose *interpolation* only, and `backend.environment:` (`docker-compose.production.yml:65-93`, verified) lists ~20 keys, none of them `CVCPKG_OIDC_*`.

### 2.6 txwtf code changes

**None.** No RFC 8628 (confirmed absent — repo-wide grep for `device_code|device_authorization|8628` returns zero), no RFC 8707, no third-party introspection, no public client, no loopback redirect.

---

## 3. Stage 1 — Light up what already exists, and harden (~280 LOC, 2 days)

**Independently shippable and independently useful:** it makes the already-written, currently-dormant Phase 13 admin SSO work in production. No new feature surface.

### Files

| Path | Change |
|---|---|
| `docker-compose.production.yml` | Add all `CVCPKG_OIDC_*` + `CVCPKG_PUBLIC_URL` + `CVCPKG_COOKIE_SECURE` under `backend.environment:` as `${VAR:-}` pass-throughs. |
| `.env.production.example` | Document the same keys with the tx.wtf values from §2.5. |
| `.github/workflows/deploy-dev.yml` | **No change needed — verified 2026-09-10.** The dev deploy does not rewrite `.env.production`; it strips and re-appends the single `CVCPKG_POPULATE_UPSTREAM=` key (`grep -v '^CVCPKG_POPULATE_UPSTREAM=' … >> …`, lines 102-104) and leaves every other line, `CVCPKG_OIDC_*` included, untouched. `deploy-prod.yml` excludes `.env.production` from rsync entirely (lines 133, 256) and errors if the host lacks it, so production secrets are host-maintained by design. |
| `src/cvcpkg/server/auth.py` | Add `derive_key(hmac_key: bytes, purpose: str) -> bytes` = `hmac.new(hmac_key, b"cvcpkg/v1/" + purpose.encode(), sha256).digest()`. **Leave `_hash_token` on the raw key** — changing it invalidates every live `cvctok_` in the fleet. |
| `src/cvcpkg/server/admin_ui.py` | `_SESSION_TTL_SECONDS` becomes `int(os.environ.get("CVCPKG_SESSION_TTL_SECONDS", 8*3600))`. Add `cookie_kwargs()` returning `dict(httponly=True, samesite="lax", secure=_cookie_secure(), path="/")`. |
| `src/cvcpkg/server/app.py` | (a) `secure=` on both cookie mint sites (`:6064`, `:6194`) and the txn cookie (`:6113`); (b) apply `_C_IDENTIFIER_RE` in `/admin/tokens/create` (`:6302`); (c) startup log line naming which of the four required OIDC vars are set/missing; (d) startup assertion refusing `user` in any group map; (e) cache the discovery doc (fetched twice per login, `:6094` and `:6162`) with a 5-minute TTL. |
| `src/cvcpkg/server/models.py` | `AuditAction.login` and `AuditAction.logout`. Both dashboard logins currently record `token_create` with target `"admin-ui"` ("closest existing action", `:6053`). |
| `src/cvcpkg/cli/_install.py` | Fix the leak at `:207-210` — `os.environ["CVCPKG_TOKEN"] = token` with no cleanup. Add `ctx.call_on_close(...)` in the same shape as `cli/__init__.py:215`. |
| `src/cvcpkg/envfile.py` | Export `warn_if_world_readable` (currently `_warn_if_world_readable`, `:130-149`) and call it from `config.load_registries()` — `registries.yaml` holds tokens with no permission check today. |
| `CHANGELOG.md` | Add an `## Unreleased` heading and the auth section the repo has owed since Phase 13 shipped 2026-07. |

### New env vars

`CVCPKG_PUBLIC_URL`, `CVCPKG_COOKIE_SECURE` (default `1` when the request scheme is https), `CVCPKG_SESSION_TTL_SECONDS`, `CVCPKG_OIDC_READER_GROUPS`.

### Tests

- `tests/unit/test_oidc.py`: extend — `user` in `ADMIN_GROUPS` raises at startup; `READER_GROUPS` tier ordering below `publisher_groups`.
- `tests/unit/test_admin_dashboard.py`: cookie carries `Secure` when `CVCPKG_COOKIE_SECURE=1`; `/admin/tokens/create` 422s on `"joe smith"` and on unicode.
- **`tests/integration/test_env_passthrough.py` (new).** Launch via `python -m uvicorn --factory cvcpkg.server.app:create_app` — the exact production path, as `tests/integration/test_authority_chain.py:111-124` already does — with `CVCPKG_OIDC_*` in the environment and assert `GET /admin/oidc/login` returns 303, not 404. **This is the regression gate for the dead-knob class**: `CVCPKG_SERVER_REQUIRE_AUTH_READS` was a silent no-op for weeks (fixed in #523, `app.py:345`/`:2090` on master) and `CVCPKG_SERVER_STORAGE_URI` still has that shape today. Every new env var in this plan gets a row in this test.

### Ship gate

```bash
curl -o /dev/null -w '%{http_code}\n' https://cvcpkg.org/admin/oidc/login   # 404 = dormant, 303 = live
```

Then have two humans actually sign in to `/admin` with tx.wtf. Everything after this builds on a proven OIDC leg.

---

## 4. Stage 2 — Principals, sessions, and the credential seam (~700 LOC + 400 test, 4 days)

No CLI yet. Useful on its own: the dashboard session becomes subject-carrying and server-side revocable, and the audit log starts naming real people instead of the literal string `"admin-ui"` (`app.py:6301`, `:6346`, `:6367`).

### 4.1 New files

**`src/cvcpkg/server/principals.py`** (~200 LOC) — pure, no DB import, unit-testable like `limits.py`:
- `sanitize_principal_name(claims) -> str` — from `preferred_username`, else email local-part, else `u{sub}`; matched against `_C_IDENTIFIER_RE` (`^[A-Za-z_][A-Za-z0-9_\-]*$`); truncated to 255.
- `collision_suffix(base, taken) -> str` — `joe`, `joe-2`, `joe-3`…
- `is_reserved_name(name, reserved) -> bool`.

**`src/cvcpkg/server/sessions.py`** (~300 LOC) — `DbSessionStore`:
- `mint(principal_id, role, *, device_label, client_id, ip, ttl, refresh_ttl, max_lifetime) -> (cvcses_, cvcref_)`
- `verify(raw) -> TokenRecord | None` — returns `TokenRecord(name=<principal.name>, role=…, token_hash=…, email=…, expires_at=…, via_previous_hash=False, credential_kind="session", session_id=…)`. Re-checks `principals.disabled`.
- `rotate(refresh_raw) -> (cvcses_, cvcref_) | None` — atomic burn `UPDATE sessions SET refresh_used=true WHERE id=:id AND refresh_used=false AND revoked=false`, rowcount 1 or family-revoke + `invalid_grant`. Successor keeps the original `max_lifetime_at`.
- `revoke(session_id)`, `revoke_all_for_principal(principal_id)`, `list_for_principal(principal_id)`, `expire_stale()`.
- All hashes are `HMAC(derive_key(hmac_key, "session"), raw)`.

**`src/cvcpkg/server/identities.py`** (~150 LOC) — `DbPrincipalStore`:
- `upsert_from_claims(issuer, subject, claims) -> PrincipalRow` — resolves on `(issuer, subject)`, **never on email**. Email is unvalidated free text, self-settable (`models.py` `EmailUpdateRequest.email: str`), publicly enumerable (`GET /v1/users/by-email` is unauthenticated by design), and `get_profile_by_email` returns the *first* active match. Auto-linking on it is privilege escalation by whoever types the address first.
- `reserved_names() -> set[str]`, `by_name(name)`, `disable(principal_id)`.

### 4.2 Schema — migration `2026_09_XX_028_principals_and_sessions.py`

Next after `2026_08_04_027_add_disk_aware_scheduling.py` (verified latest).

```python
principals(
  id            Integer PK,
  name          String(255) NOT NULL,          # UNIQUE — the identity string
  issuer        String(255) NOT NULL,
  subject       String(255) NOT NULL,
  email         String(255) NOT NULL DEFAULT '',
  display_name  String(255) NOT NULL DEFAULT '',
  last_role     String(32)  NOT NULL DEFAULT 'reader',
  disabled      Boolean     NOT NULL DEFAULT false,
  created_at, last_login_at,
  UniqueConstraint('name',  name='uq_principals_name'),
  UniqueConstraint('issuer','subject', name='uq_principals_identity'),
)

sessions(
  id                 Integer PK,
  principal_id       FK -> principals.id ON DELETE CASCADE, index,
  token_hash         String(64) NOT NULL UNIQUE,
  refresh_hash       String(64) NULL UNIQUE,
  refresh_family     String(64) NULL, index,
  refresh_used       Boolean NOT NULL DEFAULT false,
  role               String(32)  NOT NULL,
  device_label       String(128) NOT NULL DEFAULT '',
  client_id          String(64)  NOT NULL DEFAULT '',
  ip_at_issue        String(64)  NOT NULL DEFAULT '',
  issued_at, expires_at (index), refresh_expires_at, max_lifetime_at,
  revoked            Boolean NOT NULL DEFAULT false,
)
```

`tokens` and `org_members` are **not touched.** No backfill.

### 4.3 `src/cvcpkg/server/app.py`

**The seam.** Add next to `_extract_token` (`:646`):

```python
async def _verify_credential(raw: str) -> TokenRecord | None:
    """Verify any cvcpkg bearer credential.

    cvctok_  -> DbTokenStore/TokenStore (machines; unchanged)
    cvcses_  -> DbSessionStore (humans; TokenRecord.name is the principal, so
                every downstream authorization site — is_member, published_by,
                claimed_by, the audit actor — behaves identically to a token
                literally named after the person).
    """
    if raw.startswith("cvcses_"):
        return await _db_sessions.verify(raw) if _use_db else None
    if _use_db:
        return await _db_tokens.verify(raw)
    return _get_state().tokens.verify(raw)
```

Replace all ten inline verify expressions with it: lines **676/678, 706/708, 726, 735/737, 4944, 5008/5010, 5048/5050, 5098/5100, 5124**. Leave **6037/6040** (`/admin/login`) on `cvctok_` only — that is the break-glass path.

Add `_reject_session(actor)` (mirroring `_reject_grace_secret`, `:740`) on the three token self-service routes (`PATCH /v1/tokens/{name}/email`, `.../profile`, `POST .../rotate`) so a session can never manage a token, even hypothetically.

`src/cvcpkg/server/models.py` — add two transient fields to `TokenRecord`, following the existing `via_previous_hash` precedent:

```python
credential_kind: str = Field(default="token", exclude=True)
session_id: int | None = Field(default=None, exclude=True)
```

**Generalised OIDC entry.** New routes; the old ones become thin wrappers:

| Method + path | Behaviour |
|---|---|
| `GET /auth/oidc/login?next=<path>` | Validates `next` against a literal allow-list of local prefixes: `/admin`, `/link`, `/v1/auth/resume/`. Signs `{state, verifier, nonce, next}` into `cvcpkg_oidc_txn` with `path="/"`, `secure`, `httponly`, `samesite=lax`. 303 to IdP. |
| `GET /auth/oidc/callback` | **The one registered redirect URI.** `compare_digest` on state → `exchange_code` → **decode the `id_token` payload and compare `nonce`** → `fetch_userinfo` → `map_claims_to_role` → `DbPrincipalStore.upsert_from_claims` → mint `cvcpkg_session` cookie → audit `AuditAction.login` with the real subject → 303 to `next`. |
| `GET /admin/oidc/login` | 307 → `/auth/oidc/login?next=/admin`. |
| `GET /admin/oidc/callback` | 307 → `/auth/oidc/callback` preserving the query. |
| `POST /auth/logout` | Revokes the browser session row, clears the cookie, audits `AuditAction.logout`. |

`next` **must** ride inside the signed txn cookie, never as a raw query parameter on the callback. An open redirect here is a full account-takeover primitive; the allow-list is literal prefixes, not a regex.

**Browser session cookie.** `cvcpkg_session`, value `v1.<b64url(json)>.<hmac_hex>`, payload `{sub, iss, principal, role, sid, iat, exp}`, signed with `derive_key(hmac_key, "user-session")`, backed by a `sessions` row so it is server-side revocable. Replaces the subject-less `<exp>.<hmac>` (`admin_ui.py:31-35`). `_has_admin_session` becomes `_current_session(request) -> dict | None`; the `/admin` gate becomes `role == "admin"`. Keep `path="/admin"` in this stage — widening to `/` waits for CSRF in Stage 4.

**`src/cvcpkg/server/db_stores.py` — the reserved-name guard.** In `DbTokenStore.create()` (`:107-155`), immediately before the existing active-name check:

```python
if name in await _reserved_principal_names():
    raise ValueError(
        f"'{name}' is a reserved identity name (an SSO principal); "
        "choose another name"
    )
```

This is **load-bearing, not hygiene.** Revoke flips a boolean and cascades nothing (`auth.py`), `org_members` has no FK to `tokens` (`db.py:269-292`), `uq_tokens_active_name` covers only non-revoked rows, and `CVCPKG_REGISTRATION_MODE` defaults to `"open"` (`app.py:339`). Without the guard, anyone can `POST /v1/register` a token named `joe` and inherit every membership, ownership row, `published_by` entry and audit trail. Placing it in `create()` covers `POST /v1/tokens`, `POST /v1/register`, token-request approve **and** `POST /admin/tokens/create` in one edit.

**`src/cvcpkg/server/oidc.py`** — add:
- `reader_groups` to `OidcConfig` + `CVCPKG_OIDC_DEFAULT_ROLE`; `map_claims_to_role` precedence becomes admin_groups → admin_emails → publisher_groups → reader_groups → `default_role` → `None`.
- `id_token_nonce(id_token) -> str | None` — split on `.`, base64url-decode the middle segment, `json.loads`, return `nonce`. **Docstring must say plainly: this does not verify the signature; it is a mix-up check on a token already obtained over direct TLS with client authentication. Signature verification is `CVCPKG_OIDC_VERIFY_ID_TOKEN`, Stage 4.**
- Remove the `if role != "admin"` gate at the dashboard (`app.py:6171-6182`) so `CVCPKG_OIDC_PUBLISHER_GROUPS` stops being a dead knob; the `/admin` pages gate on `role == "admin"` from the session instead.

### 4.4 Tests

- `tests/unit/test_sessions.py` — mint/verify round trip; `TokenRecord.name == principal.name`; expired; revoked; disabled principal; refresh burn is atomic (two concurrent rotates, exactly one wins); reuse revokes the family; successor inherits `max_lifetime_at`.
- `tests/unit/test_principals.py` — name sanitization against `_C_IDENTIFIER_RE`; collision suffixing; **never resolves on email**; `(issuer, subject)` is the key.
- `tests/unit/test_credential_seam.py` — **the critical one.** A session for principal `joe` and a legacy token named `joe` must produce *identical* results from `is_member`, `is_owner`, `member_org_slugs`, `get_bundles(caller_token_name=…)`, `get_catalog_dict`, `list_orgs`, `list_jobs(visible_to=…)`. Parametrize over all eleven `OrgMemberRow.token_name` sites.
- `tests/unit/test_reserved_names.py` — one case per mint path: `POST /v1/tokens`, `POST /v1/register`, token-request approve, `POST /admin/tokens/create` — each 409s on a reserved principal name.
- `tests/unit/test_oidc.py` — `next` allow-list rejects `https://evil/`, `//evil`, `/admin/../x`; nonce mismatch refuses login.

---

## 5. Stage 3 — The broker and the CLI (~1,100 LOC + 500 test, 6 days)

**This is the deliverable.**

### 5.1 New server files

**`src/cvcpkg/server/pairing.py`** (~180 LOC, pure, no DB):
```python
USER_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"   # 30 chars, no 0/O/1/I/L/U
def new_user_code() -> str                 # 8 chars via secrets.choice, rendered "XXXX-XXXX"
def normalize_user_code(raw) -> str | None # upper, strip non-alphabet, None unless len == 8
def new_pairing_id() -> str                # secrets.token_urlsafe(32)
def hash_user_code(key, code) -> str       # HMAC(derive_key(k,"usercode"), code)
def hash_pairing_id(key, pid) -> str       # HMAC(derive_key(k,"pairing"), pid)
def hash_verifier(v) -> str                # plain sha256 — the CLIENT computes this too
def next_interval(current, strikes) -> int # RFC 8628 §3.5 slow_down backoff
```
Entropy: 30⁸ ≈ 6.56×10¹¹ ≈ **39.3 bits**. Sized against a 600s TTL, 5 submissions/min/IP, and a hard lockout after 10 cumulative failures per IP persisted in `code_attempts`. Alphabet and length are single constants; 10 chars buys 49 bits if ever needed.

**`src/cvcpkg/server/cli_auth.py`** (~250 LOC):
```python
def loopback_redirect_ok(uri: str) -> bool:
    p = urlsplit(uri)
    return (p.scheme == "http"
            and p.hostname in ("127.0.0.1", "::1")      # NOT "localhost" — RFC 8252 §8.3
            and p.path == "/callback"
            and not p.query and not p.fragment
            and p.port is not None and 1024 <= p.port <= 65535)
```
Port is ignored for *policy* (our own RFC 8252 §7.3 matcher — we can do this because tx.wtf never sees a loopback URI) but **bound to the issued code**, so the exchange must present the identical `redirect_uri` string. Plus `verify_pkce_s256`, `issue_cli_code`, `redeem_cli_code` (SHA-256 at rest, atomic single-use burn), and the `CLI_CLIENTS` constant — a built-in `{"cvcpkg-cli": {"public": True, "redirect": "loopback"}}`, **not a DB table and never a wildcard**.

**`src/cvcpkg/server/link_ui.py`** (~260 LOC) — server-rendered Bulma pages in `admin_ui.py`'s style, no SPA: `code_entry_html`, `confirm_html`, `result_html`. Only JS is an input mask.

### 5.2 Schema — migration `2026_09_XX_029_cli_auth.py`

```python
cli_pairings(id PK, pairing_hash String(64) UNIQUE, user_code_hash String(64) index,
             verifier_hash String(64), status String(16) default 'pending',
             requested_role String(32), granted_role String(32),
             device_label String(128), client_version String(32), platform String(64),
             client_ip String(64), principal_id FK->principals.id NULL, ttl_days Integer NULL,
             interval_seconds Integer default 5, slow_down_strikes Integer default 0,
             last_poll_at, created_at, expires_at index, approved_at, collected_at)

cli_auth_codes(id PK, code_hash String(64) UNIQUE, principal_id FK, role String(32),
               code_challenge String(43), redirect_uri Text, device_label String(128),
               ttl_days Integer NULL, expires_at, used Boolean default false)

cli_login_txns(txn_id String(64) PK, client_id String(64), redirect_uri Text, cli_state Text,
               code_challenge String(43), requested_role String(32),
               device_label String(128), expires_at)

code_attempts(client_ip String(64) PK, window_start, failures Integer default 0, locked_until NULL)
```

### 5.3 Server endpoints

**Data plane — `/v1/auth/*` (no auth dependency except where noted)**

| Method + path | Request | Response |
|---|---|---|
| `POST /v1/auth/device` | `{"client_id":"cvcpkg-cli","device_label":"catx-03","platform":"linux-x86_64","client_version":"2.1.0","verifier_hash":"<sha256 hex>","requested_role":"publisher"\|null}` | `200 {"pairing_id":"<urlsafe32>","user_code":"7QK4-M2XZ","verification_uri":"https://cvcpkg.org/link","verification_uri_complete":"https://cvcpkg.org/link?code=7QK4-M2XZ","expires_in":600,"interval":5}` |
| `POST /v1/auth/device/token` | `{"pairing_id":"…","verifier":"<plaintext>"}` | `200` token response, **or** `400 {"error":"authorization_pending"\|"slow_down"\|"access_denied"\|"expired_token","interval":N}` |
| `POST /v1/auth/device/cancel` | `{"pairing_id","verifier"}` | `204` |
| `GET /v1/auth/authorize` | query: `client_id`, `redirect_uri`, `code_challenge`, `code_challenge_method=S256`, `state`, optional `role`, `device` | `303` → `/auth/oidc/login?next=/v1/auth/resume/<txn_id>` |
| `GET /v1/auth/resume/{txn_id}` | session cookie | `302` → `<redirect_uri>?code=<60s code>&state=<cli_state>` |
| `POST /v1/auth/token` | form: `grant_type=authorization_code&code&code_verifier&client_id&redirect_uri` **or** `grant_type=refresh_token&refresh_token&client_id` | `200` token response, or `400 {"error":"invalid_grant"}` |
| `POST /v1/auth/revoke` | `Authorization: Bearer cvcses_…`; body `{"all": bool}` | `204` |
| `GET /v1/auth/whoami` | `require_role(reader, publisher, admin)` — **the first consumer the `reader` role has ever had** (verified: `grep -c "require_role(TokenRole.reader" == 0`) | `200 {"name","role","kind":"session"\|"token","principal","issuer","subject","device","expires_at","orgs":[{"slug","role"}]}` |
| `GET /v1/auth/devices` | `require_role(...)`, self-service by principal | `200 {"devices":[{"session_id","device_label","client_id","ip_at_issue","issued_at","expires_at","current":bool}]}` |
| `DELETE /v1/auth/devices/{session_id}` | same | `204` |

**Token response** (every grant):
```json
{"access_token":"cvcses_…","token_type":"Bearer","expires_in":43200,
 "refresh_token":"cvcref_…","refresh_expires_in":2592000,
 "max_lifetime_at":"2026-09-17T15:14:00Z",
 "principal":"joe","role":"publisher","email":"joe.rivera@cyberpcangel.com",
 "issuer":"https://tx.wtf","subject":"1042","device":"catx-03","session_id":41}
```

**Browser plane** — `GET /link` (bounces to `/auth/oidc/login?next=/link` when unauthenticated), `POST /link/submit`, `GET /link/confirm/{ref}`, `POST /link/approve`, `POST /link/deny`. All mutating POSTs carry a CSRF token `HMAC(derive_key(k,"csrf"), sid + ":" + ref)` compared with `compare_digest`, plus an `Origin`/`Referer` check.

Add `WWW-Authenticate: Bearer realm="cvcpkg", error="invalid_token"` to the 401s in `require_role`, `optional_reader_auth`, `optional_token`, so the client can distinguish refresh-me from forbidden.

### 5.4 Client files

**`src/cvcpkg/credentials.py`** (~170 LOC) — stdlib + PyYAML only.

Path: `$CVCPKG_CREDENTIALS_FILE`, else `<config_dir>/credentials.yaml` where `config_dir` is the existing XDG-aware `config._default_config_dir()` (`config.py:198-202`, verified). **Keyed by host**, deliberately the same shape as `registries.yaml` (`config.py:283-341`) because `authorize_request`'s decision is already a host lookup (`_server_schemes()`, `config.py:72-91`).

```yaml
version: 1
hosts:
  cvcpkg.org:
    access: cvcses_…
    refresh: cvcref_…
    expires_at: "2026-09-11T03:14:00Z"
    refresh_expires_at: "2026-10-10T15:14:00Z"
    max_lifetime_at: "2026-09-17T15:14:00Z"
    principal: joe
    role: publisher
    issuer: https://tx.wtf
    subject: "1042"
    device: catx-03
    session_id: 41
    server_url: https://cvcpkg.org
  pkg.tx.wtf:
    …
```

Written with `os.open(path, O_CREAT|O_WRONLY|O_TRUNC, 0o600)` then atomic rename; re-`chmod` on every write; the POSIX-bits branch skipped on Windows exactly as `envfile.py:138-139` does. Reads call the shared `warn_if_world_readable` (advisory, never fail-closed). API: `load()`, `get(host)`, `save(host, cred)`, `remove(host)`, `token_for(host) -> str`.

**`src/cvcpkg/oauth_native.py`** (~200 LOC, **stdlib only**) — `http.server.HTTPServer` bound to `("127.0.0.1", 0)`, one-shot handler capturing `?code&state`, static "you can close this tab" reply, `shutdown()`; `webbrowser.open()`; PKCE via `secrets` + `hashlib` + `base64`; POSTs via `urllib.request`. **Not httpx** — the repo documents that the client path loads only `click` and `PyYAML` at runtime (`haikuhost.py:11-19`, `docs/haikuports-integration.md:83-84`), and that property is what makes Haiku, OpenBSD and the single-binary client work.

**`src/cvcpkg/cli/_auth.py`** (~350 LOC) — registered by adding `_auth` to the import tuple at `cli/__init__.py:281-302` and the names to the `"Server, orgs, and accounts"` section at `cli/__init__.py:136-139`. Commands declare `@cli.command(...)` / `@cli.group(...)`, matching `_server.py:32`.

```
cvcpkg login  [--server URL] [--device NAME] [--role reader|publisher|admin]
              [--ttl-days N] [--browser/--no-browser] [--code] [--port N]
              [--timeout SECONDS] [--force] [--json]
cvcpkg logout [--server URL] [--all] [--local-only]
cvcpkg whoami [--server URL] [--json]
cvcpkg auth devices [--server URL] [--json]
cvcpkg auth revoke SESSION_ID [--server URL]
cvcpkg auth status                        # exit 0 iff a live credential exists
```

Mode selection: **loopback** when a browser is plausibly openable (`$DISPLAY` or `$WAYLAND_DISPLAY` set, or `sys.platform` in `darwin`/`win32`) *and* the ephemeral bind succeeds; otherwise **pairing**. `--code`/`--no-browser` force pairing; `--port N` forces loopback on a pinned port (for `ssh -L`). Poll loop honours the server's `interval`, doubles-with-jitter on `slow_down`, exits on `access_denied`/`expired_token`, and on Ctrl-C POSTs `/v1/auth/device/cancel` so the pending code dies immediately.

`--role` may only **narrow**. Widening is a 403.

**`src/cvcpkg/cli/_helpers.py`** — add the first HTTP-adjacent helper the file will contain (today it holds four option/path helpers and imports only `pathlib` and `click`, verified):

```python
def resolve_token(explicit: str, server_url: str) -> str:
    """--token > CVCPKG_TOKEN > credentials.yaml for server_url's host."""
```

**`src/cvcpkg/config.py`** — one line in `authorize_request()` (`:144`):

```python
token = os.environ.get("CVCPKG_TOKEN", "").strip() or _credentials_token_for(host)
```
with the `credentials` import kept function-local. This is the sole origin-scoped chokepoint for both `catalog._fetch_url` and `installer._download_from_url`, and the existing http→https upgrade (`config.py:126-145`) now protects the stored session too. **Private-org `cvcpkg install` becomes flag-free with the leak protections inherited for free.**

**`src/cvcpkg/cli/__init__.py`** — a second eager root-group step immediately after `_load_env_file`, bridging the credential for `default_server_url()`'s host into `os.environ["CVCPKG_TOKEN"]` when nothing else set it, with the same `ctx.call_on_close` cleanup. This is the proven pattern that let one `--env-file` option serve all `--token` sites without touching them. It handles the single-server case; `resolve_token(explicit, server_url)` handles `--server` pointing elsewhere. Adopt `resolve_token` module-by-module in Stage 4 — **no Bearer-construction site changes in Stage 3.**

Transparent refresh: on `401` carrying `WWW-Authenticate: … error="invalid_token"`, refresh once at `POST /v1/auth/token`, **persist the rotated refresh token** (dropping the successor locks the user out), retry once. On refresh failure print `cvcpkg: session expired — run 'cvcpkg login'` and exit **77**.

### 5.5 New env vars

`CVCPKG_SESSION_MAX_LIFETIME_SECONDS` (default 604800), `CVCPKG_REFRESH_TTL_SECONDS` (default 2592000), `CVCPKG_CLI_MAX_ROLE` (default `admin` — see §0), `CVCPKG_CLI_PAIRING_TTL_SECONDS` (600), `CVCPKG_CLI_POLL_INTERVAL` (5), `CVCPKG_CLI_DEFAULT_TTL_DAYS`. Client: `CVCPKG_CREDENTIALS_FILE`. **Every one gets a row in `tests/integration/test_env_passthrough.py` and a line in `docker-compose.production.yml`.**

### 5.6 Tests

- `tests/unit/test_pairing.py` — code entropy/alphabet; normalisation (lowercase, spaces, hyphens, `O`→reject); burn atomicity; `slow_down` backoff; expiry; lockout after 10 failures.
- `tests/unit/test_cli_auth.py` — loopback matcher accept/reject table (ephemeral port ✓; `localhost` ✗; `https` ✗; query string ✗; wrong path ✗; port < 1024 ✗); PKCE mismatch → `invalid_grant`; code replay → `invalid_grant` on the second call; `redirect_uri` port mismatch at exchange → `invalid_grant`; role narrowing ✓ / widening 403; `CVCPKG_CLI_MAX_ROLE` ceiling enforced.
- `tests/unit/test_credentials.py` — 0600 on POSIX; world-readable warning; precedence ladder `--token` > env > env-file > credentials > anonymous; `logout` removes the host entry even when the server-side revoke call fails.
- `tests/unit/test_oauth_native.py` — drive the listener against an in-process stub AS on `:0`; assert the raw `cvcses_` **never appears in any URL** the client constructs.
- `tests/integration/test_login_flow.py` — both grants end to end against the stubbed IdP from `tests/unit/test_oidc.py`, launched through `python -m uvicorn --factory`.

---

## 6. Stage 4 — Hardening and polish (~600 LOC + 250 test, 3 days)

- Widen `cvcpkg_session` to `path="/"` — **only now**, after CSRF exists — and retrofit the CSRF helper onto the three currently-unprotected `/admin` POSTs (`/admin/packages/action`, `/admin/tokens/create`, `/admin/tokens/revoke`), which today rely solely on `SameSite=Lax`.
- `POST /v1/stream-ticket` → single-use 60s ticket, replacing the raw bearer in the EventSource URL (`landing.py:3022-3023`), where it lands in proxy logs, `Referer` and browser history. Retire the four `localStorage.getItem('cvcpkg_token')` paste boxes on Builders/Builds/Recipes/Orgs.
- Add a **Sign in** item to the navbar (`landing.py:279-340` has none, and `/admin` is entirely unlinked).
- Surface principals in `GET /v1/users` so an org owner can discover the name to pass to `cvcpkg org add-member`. Move `/v1/users*` behind `optional_reader_auth` and drop `email` from the anonymous view — after this feature those names map to real humans, which makes an unauthenticated listing a people-directory.
- Route the 43 hand-rolled Bearer sites through `resolve_token`, module by module. Leave `backends/gh_release.py:36-38` alone — that Bearer is a `GITHUB_TOKEN`.
- `CVCPKG_OIDC_VERIFY_ID_TOKEN=1` — full RS256/JWKS with `kid` selection and refetch-on-`kid`-miss, `PyJWT>=2.8` in the `[server]` extra only (already in `poetry.lock` at 2.13.0 via `azure-identity`→`msal`; its heavy half `cryptography>=41.0` is already a mandatory main dependency at `pyproject.toml:53`), `"jwt"` added to `packaging/cvcpkg.spec:76` client excludes. Separate PR, carries the roadmap amendment.
- Docs: new `docs/cli-login.md`; rewrite `docs/oidc-identity.md` (tick follow-ups #1 and #2 at `:104-108`); runbook sections for client-secret rotation and `revoke_all_for_principal`; roadmap update; CHANGELOG.

---

## 7. Dependencies

**Client: zero new packages.** `http.server`, `webbrowser`, `urllib.request`, `secrets`, `hashlib`, `base64`, `json` — all stdlib, all present on Windows, macOS, Linux, OpenBSD and Haiku. `packaging/cvcpkg.spec` unchanged.

**Server, Stages 1–3: zero new packages.** `httpx>=0.24` is already a hard dependency and `oidc.py` already uses it.

**Server, Stage 4 only:** `PyJWT>=2.8` in the `[server]` extra, lazily imported.

**Never:** `authlib` (txwtf deliberately dropped it in `89579c7`; reintroducing it here contradicts an explicit sibling-repo decision) or `python-jose` (unmaintained, absent from both `pyproject.toml` and `poetry.lock`).

---

## 8. Machine and CI tokens — unchanged, byte for byte

- `cvctok_` verification is untouched: same `HMAC-SHA256(raw, hmac_key)`, same `tokens` table, same rotation grace window, same `via_previous_hash` handling, same six `allow_grace=True` routes. `_verify_credential` only adds a `cvcses_` prefix branch *before* it.
- `--token` and `CVCPKG_TOKEN` **win over** the credential file, always. A runner exporting `CVCPKG_TOKEN` never consults `credentials.yaml` and sees no behaviour change.
- `--env-file` (`envfile.py`, `cli/__init__.py:190-231`) is unchanged and remains the recommended CI delivery, keeping tokens out of argv.
- `cvcpkg-server bootstrap` / `token create` (`server/cli.py`) unchanged. `POST /v1/tokens`, `POST /v1/register`, token-request approve unchanged except for the reserved-name guard.
- `builder_fleet.py` unchanged: per-server `token`/`token_env` from `fleet.yaml`, passed as `--token` on argv. **Builders are machines; they stay on `cvctok_`.** Document that `cvcpkg login` is not for builders.
- `deploy-prod.yml`'s `CVCPKG_PUBLISHER_TOKEN` unchanged.
- `cvcpkg login` in a non-TTY prints the code to stderr, blocks, and on timeout exits non-zero with `no approval received — CI should use CVCPKG_TOKEN, see docs/cli-login.md`. Fail loud, fail with the right advice.

This preserves roadmap design principle #5 verbatim (`CVCPKG-ROADMAP.md:3589-3594`): HMAC tokens for machines, delegated OIDC for humans.

---

## 9. What happens on an SSH-only headless box

**`cvcpkg login` just works, with no browser, no port bind, no `DISPLAY`, no `ssh -L`.** It needs only outbound HTTPS from the remote host, and the credential lands **on the remote box** where it is needed.

```
tfx@catx-03:~$ cvcpkg login
Pairing this machine with https://cvcpkg.org

  1. On any device, open:  https://cvcpkg.org/link
  2. Enter this code:      7QK4-M2XZ

  (or open directly: https://cvcpkg.org/link?code=7QK4-M2XZ)

Code expires in 10:00. Waiting for approval... (Ctrl-C to cancel)
```

The human reads 8 characters off the terminal and types them into whatever browser they already have — laptop, phone, the machine they SSH'd from. tx.wtf sign-in (usually two silent redirects, since `OIDCConsent` persists), one Approve click, and within ~5s the terminal completes itself.

Why this direction and not the other: the code flows **terminal → human → browser**, and the credential returns over HTTPS. The alternative (`--manual` OOB) would push a 43-character case-sensitive `token_urlsafe(32)` **browser → terminal** inside a 60-second TTL. That is not a workflow, it is a race — and it fails outright when the browser is a phone.

Two supporting paths:
- **`ssh -L 49812:127.0.0.1:49812 host` + `cvcpkg login --port 49812`** for anyone who prefers loopback. Works only because tx.wtf never sees the loopback URI — our own matcher is port-agnostic. Documented, not the default.
- **Air-gapped / no outbound HTTPS at all:** `cvcpkg login` cannot work and does not pretend to. `cvcpkg auth status` exits non-zero; an admin mints a `cvctok_` out of band. Note the request path never leaves the server either: `cvcses_` is verified locally against cvcpkg's own DB with an HMAC compare, so tx.wtf is **not** in the availability path of any cvcpkg API call — only of *new* logins.

Anti-phishing on the pairing path (RFC 8628 §5.4 documents that this channel is socially engineerable):
- The confirmation screen is **never skipped**, even from `verification_uri_complete`.
- It shows requester IP, claimed hostname, platform, CLI version and elapsed time, with explicit "if you did not just run `cvcpkg login`, click Deny" copy.
- Role defaults to the **lowest** the user is entitled to; raising it requires an active choice, and role/TTL live behind an **Advanced** disclosure so the common case is one click.
- `CVCPKG_CLI_MAX_ROLE` defaults to `admin` (§0), so a paired session **can** be an admin
  session. That makes the two mitigations above load-bearing rather than belt-and-braces:
  the confirmation screen is never skipped, and elevating a session to `admin` requires a
  deliberate choice on that screen — it is never the default the CLI asks for.
- Every start/approve/deny/collect is audited with the real subject.

Bounded, and strictly narrower than what already ships: `POST /admin/tokens/create` today lets any cookie session mint an **admin** bearer token of its choosing (`app.py:6282-6314`, gated only by a subject-less cookie).

Because loopback is the desktop default, the transcription channel is not the *only* path — which is what the security judge objected to about a pairing-only design.

---

## 10. Security notes that are load-bearing

**Deprovisioning.** A disabled tx.wtf account loses cvcpkg access within `CVCPKG_SESSION_MAX_LIFETIME_SECONDS` (7 days default) with **zero operator action**, because refresh cannot cross the horizon and re-login re-reads `/oauth/userinfo`. `DbSessionStore.verify()` additionally re-checks `principals.disabled` on every request, so an admin `disable` is instant. Compare a 30-day or 90-day static bearer, whose only remedy is a human noticing.

**The pairing verifier is client-generated.** `pairing_id` crosses the wire twice and can land in a proxy access log or an APM trace; on its own it must not collect a credential. The CLI generates `verifier = secrets.token_urlsafe(32)`, registers only `sha256(verifier)`, and presents plaintext at collection. Strictly stronger than RFC 8628, where `device_code` alone suffices.

**The token is minted on collection, not on approval.** An approved-but-never-collected pairing leaves no credential anywhere.

**Key separation.** `derive_key(hmac_key, purpose)` for `"session"`, `"pairing"`, `"usercode"`, `"csrf"`, `"user-session"`. API-token hashing deliberately stays on the raw key — changing it invalidates every live `cvctok_` in the fleet.

**Brute force is DB-backed.** `code_attempts` in Postgres, not `_check_rate_limit`'s in-process per-worker window (`app.py:2022-2037`), which resets on restart and would multiply the guess budget by N the day anyone scales workers. The polling endpoint gets its own tier — a 5-second poll against a per-minute write window trips instantly.

**Not addressed here, flag in the PR, fix separately.** The builder-authz gaps make `publisher` far stronger than its name suggests: `POST /v1/builders/register` accepts a caller-supplied `org_slug` with **no** membership check (contrast `POST /v1/builds`, which calls `is_member`); `PATCH /v1/builders/{id}` lets any publisher token rewrite any builder's `served_namespaces`, which `_choose_builder` trusts for namespace isolation; `next-job`/WS/heartbeat authenticate any publisher token against any `builder_id`, and the WS displaces the real builder's socket. **Triage these before populating `cvcpkg-publisher` widely.** They are orthogonal to login, but this feature hands that role out via a group map.

---

## 11. Open questions — real forks that need a human

The role/CLI-ceiling/testing/registration forks were resolved on 2026-09-10; see §0.
What remains:

1. **Flip `CVCPKG_REGISTRATION_MODE` to `admin-gated` on cvcpkg.org?** Open registration plus an `add_member()` that never checks the token exists already permits pre-seeding memberships for unclaimed names. The Stage 2 reserved-name guard closes the SSO half; flipping the mode closes the rest. It is a user-visible policy change and arguably owed regardless.
2. **Does the pkg.tx.wtf mirror do SSO at all?** It is a read-only mirror on catx-03 running the same compose file. Probably not — but if not, its `.env.production` must be left alone and the docs must say so.

---

## 12. Sequencing and confidence

| Stage | LOC (impl + test) | Days | Independently shippable? |
|---|---|---|---|
| 1 — enable + harden | 280 + 150 | 2 | **Yes** — turns on the already-written admin SSO |
| 2 — principals, sessions, seam | 700 + 400 | 4 | **Yes** — subject-carrying dashboard sessions, real audit actors |
| 3 — broker + CLI | 1,100 + 500 | 6 | **Yes** — this is the deliverable |
| 4 — hardening + polish | 600 + 250 | 3 | Yes |

**~2,700 impl + ~1,300 test, ~15 working days.** The critical path runs through other people, not code: the tx.wtf client registration, the `TXWTF_RATE_LIMIT_EXEMPT_IPS` exemption, the group-taxonomy decision, and the dev-TLS question. **Start all four on day one.** Stage 1 cannot ship without the first two.

Confidence is high for Stages 1, 2 and 4 — all local, bounded, and testable against a stub IdP. Stage 3's client half is the schedule risk despite its line count: `webbrowser` behaviour and loopback binding need real verification on Windows, macOS, OpenBSD and Haiku, and the pairing path needs exercising over an actual SSH session.

One PR per stage, on a branch off `origin/master`, no commits to the default branch, no Claude attribution trailers.