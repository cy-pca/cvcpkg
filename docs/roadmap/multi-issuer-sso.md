# Multi-issuer SSO — cvcpkg as a client of several tx.wtf sites / federation rings

Today a cvcpkg instance is an OIDC client of **exactly one** issuer. This design
makes it a client of **several** simultaneously, so users from different tx.wtf
deployments (or federation rings) can each sign in to the same cvcpkg, pick their
provider at login, and land as distinct, correctly-scoped identities.

Status: **IMPLEMENTED.** It follows and reuses the Stage 1–3 work in
`txwtf-sso-and-cli-login.md`. One deviation from the original design below: extra
providers are configured as a single **`CVCPKG_OIDC_EXTRA_PROVIDERS` JSON array**
rather than indexed `CVCPKG_OIDC_<ID>_*` env vars — a docker-compose `${VAR:-}`
passthrough cannot enumerate an unknown set of indexed keys, and a single key is
also what the empty-string-passthrough outage taught us to prefer. The bare
`CVCPKG_OIDC_*` vars remain provider `default` exactly as designed.

---

## 0. First, the distinction that decides whether you need this

- **Rings that federate *into* one tx.wtf** — cvcpkg already works. tx.wtf is a
  federated identity network; one issuer can sit in front of many upstream
  sources, and that federation is transparent to cvcpkg (it sees one issuer).
  Prefer this when you control the tx.wtf that fronts the rings.
- **Independent tx.wtf sites / rings with *separate issuers*** — this is the gap
  this design fills: cvcpkg must be a direct RP of each issuer at once.

If every population you care about can be reached behind a single tx.wtf, do
that instead; it needs no cvcpkg change.

---

## 1. What already supports this (reuse, no migration)

The identity **data model is already issuer-scoped** — this is the load-bearing
fact that makes multi-issuer cheap:

- `principals` is unique on **`(issuer, subject)`** (`uq_principals_name` on the
  handle, `uq_principals_identity` on issuer+subject). Two different people from
  two different issuers are two different principals even if their IdP subject
  ids collide.
- `DbPrincipalStore.upsert_from_claims(issuer, subject, claims, role)` already
  takes the issuer and resolves on it, **never on email**.
- Sessions carry `iss`; the credential seam, `/account`, `/link`, org membership,
  and the CLI broker all operate on *principals*, not issuers — they are already
  issuer-agnostic and need **no change**.

So multi-issuer is almost entirely a **config + login-routing** change. No schema
migration.

## 2. What is single-issuer today (the change surface)

- `server/oidc.py` `OidcConfig.from_env()` reads one `CVCPKG_OIDC_ISSUER` + one
  `CLIENT_ID/SECRET/REDIRECT_URL` + one group map.
- `server/app.py`: `admin_oidc_login` / `admin_oidc_callback` / `auth_oidc_login`
  / `auth_oidc_callback` assume that single config; `/v1/auth/authorize` has no
  provider concept.
- `_oidc_enabled()` is a single boolean.

---

## 3. Design

### 3.1 Config: a provider registry (back-compatible)

Introduce **named providers**. Keep the bare `CVCPKG_OIDC_*` vars as the implicit
provider `default`, so every current single-issuer deployment (cvcpkg.org) keeps
working with **zero config change**. Additional providers are a single JSON array
in **`CVCPKG_OIDC_EXTRA_PROVIDERS`**:

```
CVCPKG_OIDC_DISPLAY_NAME=tx.wtf          # label for the bare "default" provider

CVCPKG_OIDC_EXTRA_PROVIDERS=[{"id":"ringb","display_name":"Ring B",
  "issuer":"https://ring-b.example","client_id":"...","client_secret":"...",
  "redirect_url":"https://cvcpkg.org/auth/oidc/callback",
  "admin_groups":["cvcpkg-admin"],"publisher_groups":["cvcpkg-publisher"],
  "reader_groups":["cvcpkg-reader"],"default_role":"reader"}]
```

Each entry needs `id`, `issuer`, `client_id`, `client_secret`, `redirect_url`;
optional `display_name`, `scopes`, `groups_claim`, `admin_groups`,
`publisher_groups`, `reader_groups`, `admin_emails`, `default_role` (group fields
accept a JSON list or a comma string). A single key (not indexed vars, not a
file) keeps one clean `${VAR:-}` passthrough line — the `test_env_passthrough`
gate has a row for it — and avoids the enumerate-unknown-keys problem compose
cannot solve. Malformed JSON, a missing required field, an empty `id`, or reusing
`id:"default"` fails the boot loudly (never a silently dropped provider).

**Types.** `OidcConfig` is kept as the per-provider type (it already *was* a
single provider's config) and gains `id` + `display_name`; renaming to
`OidcProvider` would have churned every call site for no behavioural gain. The
registry is two module functions rather than a class:

- `load_providers()` → `list[OidcConfig]`: the bare `default` (when enabled) plus
  the JSON extras; only fully-configured providers are returned.
- `validate_registry(providers)` → problems: per-provider `validate_config`
  (universal-group + default_role guards) **plus** a duplicate-issuer / duplicate-id
  guard.
- In `app.py` the list is turned into `{id: OidcConfig}` once at startup;
  `_oidc_provider(id)` / `_oidc_provider_choices()` / lookup-by-issuer are closures.
- `map_claims_to_role(claims, cfg)` is unchanged logic, per-provider maps.

### 3.2 Startup guards

- **No two enabled providers may share an `issuer` URL** — otherwise callback
  routing and `by_issuer` are ambiguous. Refuse to boot.
- The existing **"`user` group must not be mapped"** guard runs **per provider**.
- Discovery documents are cached **per issuer**.

### 3.3 Login routing (browser)

- `GET /login` and the `/admin` sign-in: **0 providers** → token-only break-glass
  (today); **1 provider** → straight to it (today's UX, unchanged); **>1** →
  render a **provider picker** ("Sign in with tx.wtf", "Sign in with Ring B").
- `GET /auth/oidc/login?provider=<id>&next=<path>` — validate `<id>` against the
  registry, run discovery for **that** issuer, and **carry `provider` inside the
  signed txn cookie** (never trust it from the callback query). `next` keeps
  today's literal allow-list (`_safe_next`), unchanged.
- **Callback:** one shared route `GET /auth/oidc/callback`. It reads `provider`
  from the **verified** txn cookie, looks up the `OidcProvider`, then
  `exchange_code` → userinfo → **nonce check** → `map_claims_to_role(claims,
  provider)` → `upsert_from_claims(provider.issuer, subject, claims, role)` →
  mint session. Because tx.wtf matches redirect URIs byte-exactly, each provider
  registers this same callback path; the provider is disambiguated by the signed
  cookie, not the URL. (Fallback if some IdP demands a distinct URI:
  `/auth/oidc/callback/<id>` per provider.)

### 3.4 CLI login routing

- `GET /v1/auth/providers` → `[{id, display_name}]` for enabled providers, so the
  CLI can present a choice.
- `GET /v1/auth/authorize` gains an optional `provider=<id>`; it is stored in the
  `cli_login_txns` row / carried into the OIDC txn so the resumed callback uses
  the right provider. Device pairing is unaffected (the human approves in a
  browser that has already chosen a provider).
- CLI: `cvcpkg login --provider <id>` (or interactive pick when >1 and a TTY;
  error asking for `--provider` when non-interactive and ambiguous).

### 3.5 Roles are per-issuer; handles are global

- Each provider carries **its own** admin/publisher/reader group maps, so each
  ring governs its own grants. `CVCPKG_CLI_MAX_ROLE` stays a single global
  ceiling.
- `principals.name` (the handle) stays **globally unique** because org membership
  keys on it. Two people who both sanitize to `joe` from different issuers get
  `joe` and `joe-2` (existing `collision_candidates`). Document this clearly: the
  **handle is first-come across all rings; the issuer disambiguates the identity,
  not the handle.** An org owner adds a member by handle, so operators should
  know handles are a shared namespace. (Deliberately not prefixing handles with
  the provider — the `.`/`:` separators are already reserved and a prefix would
  leak the ring into every membership row and audit line.)

### 3.6 Security notes (load-bearing)

- **`provider` comes only from the signed txn**, never the callback query — else
  an attacker could pair a code minted by issuer A with provider B's client
  secret/issuer (an OAuth mix-up). This mirrors why `next` and the PKCE verifier
  ride in the signed cookie today.
- Per-provider client secrets, per-provider nonce, per-provider discovery.
- The shared-callback design keeps the RFC-8252/redirect-exact-match story simple
  while staying mix-up-safe via the cookie.

---

## 4. Migration & compatibility

- **Zero migration.** No schema change; `(issuer, subject)` already namespaces.
- Existing single-issuer deployments are untouched: the bare `CVCPKG_OIDC_*`
  vars become the `default` provider.
- Rollback is config-only (remove the extra providers).

## 5. What shipped

| Area | Work |
|---|---|
| `server/oidc.py` | `OidcConfig` + `id`/`display_name`/`from_dict`; `load_providers()` (default + JSON extras, loud failures); `validate_registry()` (dup-issuer/dup-id + per-provider guards) |
| `server/app.py` | provider param on login/authorize; provider in the signed txn; callback dispatch on the txn provider only; `_oidc_enabled` → registry; `GET /v1/auth/providers` |
| UI | provider picker on `/login` + the `/admin` sign-in (server-rendered, one button per ring) |
| CLI | `cvcpkg login --provider` (interactive pick / browser-picker fallback); `cvcpkg auth providers`; consume `/v1/auth/providers` |
| Ops | `CVCPKG_OIDC_DISPLAY_NAME` + `CVCPKG_OIDC_EXTRA_PROVIDERS` in docker-compose passthrough + `.env.production.example` + `test_compose_env_passthrough` rows |
| Tests | `test_oidc_registry` (parsing, back-compat default, loud failures, dup-issuer); `test_multi_issuer_login` (picker rendering, callback picks provider from txn not query, per-issuer identity, single-provider back-compat) |

No new runtime dependencies. The data layer needed no change — `(issuer, subject)`
already namespaces every identity.

## 6. Decisions taken (were open questions)

1. **Shared callback path.** One `/auth/oidc/callback`; the provider is read from
   the signed txn, so no per-provider redirect URI is needed. (A per-provider
   `/auth/oidc/callback/<id>` remains the fallback if some IdP ever refuses to
   share a redirect URI.)
2. **Handle collisions across rings** — global-first-come + suffixing (existing
   `collision_candidates`). The handle is a shared namespace across rings; the
   issuer disambiguates the identity, not the handle. The `/account` page shows
   each principal's own issuer so a human can tell two `joe`s apart.
3. **Admin across rings** — a global admin may come from *any* ring's
   `cvcpkg-admin` (each provider carries its own group maps). Pinning server-admin
   to one "home" ring was **not** added; it would be a small extra guard if wanted.
