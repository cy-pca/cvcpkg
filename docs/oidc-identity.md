# OIDC Identity & Access

cvcpkg delegates **human** authentication to an external OIDC identity
provider rather than building account management, password handling, and
permission UX itself.  **HMAC API tokens remain the mechanism for machines**
(CI, builders, scripted publishes) — the right tool for each audience.

> This is the scoping of the "HMAC-SHA256 tokens are simpler than OAuth"
> design principle: simple tokens for machines, delegated OIDC for people.

## Flow

```mermaid
sequenceDiagram
    participant U as User (browser)
    participant C as cvcpkg-server
    participant I as OIDC provider
    U->>C: GET /admin/oidc/login
    C->>C: PKCE verifier+challenge, state, nonce<br/>→ signed HttpOnly txn cookie
    C-->>U: 303 → IdP authorize (state, nonce, S256 challenge)
    U->>I: authenticate
    I-->>U: 303 → /admin/oidc/callback?code&state
    U->>C: callback
    C->>C: verify txn cookie + state (CSRF)
    C->>I: POST token_endpoint (code, verifier, client_secret) [TLS]
    I-->>C: access_token
    C->>I: GET userinfo_endpoint (Bearer) [TLS]
    I-->>C: claims
    C->>C: map claims → role; admin? → signed session cookie
    C-->>U: 303 → /admin (logged in)
```

Standard **authorization code** flow for a confidential client, with
**state** (CSRF), **nonce**, and **PKCE S256**.

**On id_token signature verification.** Tokens are obtained by *direct
server-to-server TLS* communication with the token endpoint, so per
[OIDC Core §3.1.3.7](https://openid.net/specs/openid-connect-core-1_0.html#IDTokenValidation)
the TLS server validation stands in for checking the token signature; claims
are then read from the userinfo endpoint over TLS. This keeps cvcpkg free of a
JWT/JWKS dependency. Local id_token signature validation is a documented
hardening follow-up for deployments that require it.

**The PKCE verifier never travels in `state`** (which is visible to the IdP and
in the browser address bar) — it is carried in a short-lived (10 min),
HMAC-signed, HttpOnly transaction cookie scoped to `/admin`.

## Configuration

| Variable | Meaning |
|---|---|
| `CVCPKG_OIDC_ISSUER` | Provider base URL, e.g. `https://accounts.example.com` |
| `CVCPKG_OIDC_CLIENT_ID` | Client ID |
| `CVCPKG_OIDC_CLIENT_SECRET` | Client secret (confidential client) |
| `CVCPKG_OIDC_REDIRECT_URL` | e.g. `https://cvcpkg.org/admin/oidc/callback` |
| `CVCPKG_OIDC_SCOPES` | default `openid email profile` |
| `CVCPKG_OIDC_GROUPS_CLAIM` | claim holding groups (default `groups`) |
| `CVCPKG_OIDC_ADMIN_GROUPS` | comma-separated groups granted **admin** |
| `CVCPKG_OIDC_PUBLISHER_GROUPS` | comma-separated groups granted **publisher** |
| `CVCPKG_OIDC_ADMIN_EMAILS` | comma-separated emails granted **admin** (for IdPs that emit no groups) |

OIDC is **offered only when issuer + client_id + client_secret + redirect_url
are all set**. Otherwise `/admin/oidc/*` returns 404 and the login page shows
only the token form — so an unconfigured server is unchanged.

Discovery uses `{issuer}/.well-known/openid-configuration`.

## Authorization

Claims map to a cvcpkg role in this precedence:

1. **admin groups** → `admin`
2. **admin emails** → `admin`
3. **publisher groups** → `publisher`
4. otherwise → **refused**

A user who authenticates at the IdP but matches no mapping is **refused**
(HTTP 403), never silently downgraded to a usable role. The `/admin`
dashboard requires `admin`; logins are audit-logged with the user's
email/username as the actor.

The token form always remains available for machine and break-glass access.

## Example (Google / generic)

```bash
export CVCPKG_OIDC_ISSUER=https://accounts.google.com
export CVCPKG_OIDC_CLIENT_ID=...apps.googleusercontent.com
export CVCPKG_OIDC_CLIENT_SECRET=...
export CVCPKG_OIDC_REDIRECT_URL=https://cvcpkg.org/admin/oidc/callback
export CVCPKG_OIDC_ADMIN_EMAILS=you@example.com     # Google emits no groups
```

```bash
# Enterprise IdP with groups
export CVCPKG_OIDC_ISSUER=https://keycloak.example.com/realms/main
export CVCPKG_OIDC_GROUPS_CLAIM=groups
export CVCPKG_OIDC_ADMIN_GROUPS=cvcpkg-admins
export CVCPKG_OIDC_PUBLISHER_GROUPS=cvcpkg-publishers
```

## Follow-ups

- Local id_token signature verification (JWKS) for deployments that want it
  in addition to TLS-validated direct exchange.
- OIDC-authenticated **publish** (mapping a session to a publisher identity)
  — today publishing remains token-based.
- Org membership sync from IdP groups (Phase 6 governance).
