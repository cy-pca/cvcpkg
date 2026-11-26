# CLI login (`cvcpkg login`) — server broker

cvcpkg.org acts as a small first-party **authorization server** so interactive
users can obtain a short-lived session instead of hand-cutting long-lived API
tokens. Machines keep using `cvctok_` tokens, unchanged.

## Using it

```bash
cvcpkg login                 # desktop: opens your browser (loopback)
cvcpkg login --code          # headless/SSH: prints a code to approve in a browser
cvcpkg login --role publisher --device catx-03
cvcpkg auth providers        # list the server's identity providers (if several)
cvcpkg login --provider ringb  # pick one when the server has several
cvcpkg whoami                # who am I, and which orgs
cvcpkg auth devices          # list your active sessions
cvcpkg auth revoke <id>      # revoke one session
cvcpkg logout [--all]        # revoke server-side + forget locally
cvcpkg auth status           # exit 0 iff a live session exists
```

## Choosing an identity provider (multi-issuer)

A server can be an OIDC client of **several** issuers at once (different tx.wtf
sites / federation rings — see [`roadmap/multi-issuer-sso.md`](roadmap/multi-issuer-sso.md)).
When it is, `cvcpkg login` picks the provider like this: `--provider <id>` if you
pass one; otherwise the single provider if there is only one; otherwise it prompts
on a TTY, or (for the browser/loopback flow) lets you pick on the sign-in page.
Run `cvcpkg auth providers` to see the ids and labels. When scripting a headless
login against a multi-provider server, pass `--provider` explicitly.

After `cvcpkg login`, `cvcpkg search`/`install` against a **private** org just
work: the stored session is attached (origin-scoped) for that server's host and
auto-refreshed when the access token expires. Precedence is always
`--token` > `CVCPKG_TOKEN` > the stored session, so CI is unaffected. On an
SSH-only box, `cvcpkg login` (pairing) needs only outbound HTTPS; the credential
lands on the remote box where the work runs.

The client is **stdlib-only** at runtime (`http.server`, `webbrowser`,
`urllib`) so it loads on Windows, macOS, Linux, OpenBSD and Haiku and in the
single-binary build. Credentials live in `credentials.yaml` (0600) next to
`registries.yaml`; `CVCPKG_CREDENTIALS_FILE` overrides the path.

## Credentials

- **`cvcses_…`** — an opaque access token (a *session*). It is a bearer: send it
  as `Authorization: Bearer cvcses_…` to any API. It verifies **as its
  principal**, so `cvcpkg org add-member … --user <you>` and every other
  authorization check treats it exactly like a token named after you. Role is
  clamped to your current IdP entitlement on every request, and disabling your
  principal ends every session immediately.
- **`cvcref_…`** — a single-use refresh token. Rotating it issues a new
  `(access, refresh)` pair; **reusing a spent refresh revokes the whole family**
  (the signature of a stolen credential), forcing a fresh login.

Sessions are server-side rows, so `cvcpkg logout` (revoke) actually ends them —
unlike a stateless signed token.

## Grants

### Device pairing (headless / SSH-only)
1. `POST /v1/auth/device` `{client_id, device_label, platform, client_version, verifier_hash, requested_role}`
   → `{pairing_id, user_code, verification_uri, verification_uri_complete, expires_in, interval}`.
   The client generates a random verifier and registers only its `sha256`.
2. The human opens `verification_uri` (`/link`), signs in, and approves the code.
   The confirmation screen is **never skipped** and shows who is asking.
3. `POST /v1/auth/device/token` `{pairing_id, verifier}` polls; returns
   `authorization_pending` until approved, then the token response **once** (the
   session is minted on collection, so an approved-but-uncollected code leaves no
   credential). `POST /v1/auth/device/cancel` ends a pending request.

### Loopback (desktop)
1. The client opens `GET /v1/auth/authorize?client_id&redirect_uri&code_challenge&code_challenge_method=S256&state[&role][&device]`.
   `redirect_uri` must be `http://127.0.0.1:<port>/callback` or `[::1]` — a literal
   loopback IP, never `localhost`.
2. cvcpkg parks the request and sends the browser through the IdP; on return,
   `GET /v1/auth/resume/{txn_id}` issues a 60-second PKCE-bound code and
   redirects to the loopback `redirect_uri?code&state`.
3. `POST /v1/auth/token` (form) `grant_type=authorization_code&code&code_verifier&client_id&redirect_uri`
   exchanges it. `grant_type=refresh_token&refresh_token` rotates.

### OAuth 2.0 clients (Ariadne's `oidc-sign-in`, cypkg)

The loopback grant is plain OAuth 2.0 (RFC 6749, PKCE per RFC 7636), so a
client that is not `cvcpkg login` can drive it from the metadata alone.
cvcpkg is an OAuth **authorization server only**: it issues no ID token and
publishes no JWKS. Its users still sign in at the identity provider (tx.wtf),
which cvcpkg is an OIDC client of.

- **Metadata** (RFC 8414) at `/.well-known/oauth-authorization-server`, and the
  same document at `/.well-known/openid-configuration`, where OpenID Connect
  clients look. `issuer` is `CVCPKG_PUBLIC_URL` (else the URL the server was
  reached at), and a client compares it exactly, so set `CVCPKG_PUBLIC_URL`.
  Any origin may read it (CORS `*`), as a browser page signing in must.
- **Clients** are a built-in list (`cli_auth.CLI_CLIENTS`), all public: no
  secret, and `token_endpoint_auth_method` `none`.
  - `cvcpkg-cli` — `cvcpkg login`; loopback only; the only device-pairing client.
  - `cypkg` — cy-pca/cypkg; loopback, and the browser page
    `https://cy-pca.github.io/cypkg/` (exactly: path and query are part of it).
    An operator adds pages with `CVCPKG_OAUTH_PAGE_REDIRECTS`.
- **`scope` is a role**: `reader`, `publisher` or `admin`; the highest one
  named is asked for, narrowed as `role` is (no scope: `reader`). `role`, the
  CLI's parameter, wins when both are given. Other scopes are ignored, except
  `openid`, which goes back to the client as `invalid_scope`: a client that
  asks for it would wait for an ID token that never comes. The token response
  says the role granted as its `scope`.
- `response_type` must be `code` (else `unsupported_response_type`). Once the
  redirect URI checks out, these errors go back to it with `state` and `iss`.
- **Replies carry `iss`** (RFC 9207), and the metadata says so
  (`authorization_response_iss_parameter_supported`).
- **Codes and refresh tokens are bound to their client**: a code is redeemed
  only with the `client_id` it was issued to, a refresh only with its
  session's. A token request with no `client_id` is the CLI's.
- **Revocation** (RFC 7009): `POST /v1/auth/token/revoke` (form) `token[&token_type_hint][&client_id]`.
  The token (a `cvcses_` access or `cvcref_` refresh token) is the proof; the
  whole sign-in goes, every rotation of it. Any other or dead token answers
  200 too; a token of another client is `unauthorized_client`.
- A browser page calls the token and revocation endpoints with `fetch`, so its
  origin must be on `CVCPKG_CORS_ORIGINS`.
- There is no RFC 8628 device grant: the device pairing above is cvcpkg's own
  protocol, and the metadata names no `device_authorization_endpoint`.

Ariadne signs in with:

```lisp
(oidc-sign-in (dict "issuer" "https://cvcpkg.org" "client_id" "cypkg"
                    "scopes" (list "publisher") "credential" "cvcpkg"))
;; then (http-get "https://cvcpkg.org/v1/auth/whoami" (dict "auth" "cvcpkg"))
```

`scopes` must be given: Ariadne's default is `openid`.

### Session management
- `GET /v1/auth/whoami` — identity, role, and org memberships (the first
  consumer the `reader` role has ever had).
- `GET /v1/auth/devices` / `DELETE /v1/auth/devices/{session_id}` — list/revoke
  your sessions.
- `POST /v1/auth/revoke` `{all?}` — sign out this session, or all of them.

## Configuration (env)

| Var | Default | Meaning |
|---|---|---|
| `CVCPKG_SESSION_TTL_SECONDS` | `43200` (12h) | Access-token lifetime |
| `CVCPKG_REFRESH_TTL_SECONDS` | `2592000` (30d) | Refresh-token lifetime |
| `CVCPKG_SESSION_MAX_LIFETIME_SECONDS` | `604800` (7d) | Hard reauth horizon — refresh cannot cross it |
| `CVCPKG_CLI_MAX_ROLE` | `admin` | Ceiling on a CLI session's role (deployments may lower it) |
| `CVCPKG_CLI_PAIRING_TTL_SECONDS` | `600` | How long a user code is valid |
| `CVCPKG_CLI_POLL_INTERVAL` | `5` | Minimum device poll interval (seconds) |
| `CVCPKG_PUBLIC_URL` | — | Used to build the absolute `verification_uri`; the OAuth `issuer` |
| `CVCPKG_OAUTH_PAGE_REDIRECTS` | — | More browser pages a client with pages may be sent back to: comma-separated `CLIENT_ID=https://...`, matched exactly |

A CLI session's role only ever **narrows** to the requesting principal's
entitlement and the `CVCPKG_CLI_MAX_ROLE` ceiling; it never widens.
