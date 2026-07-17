# Token Rotation

Rotate an API token's secret **in place** — without touching anything
keyed to the token's name (role, expiry, org memberships, audit
history, GitHub secret names). Rotation is the low-risk answer to
"this secret may have leaked" and the routine answer to "we rotate
credentials every N days".

## Why rotate instead of create + revoke?

Before rotation existed, replacing a credential meant:

1. `cvcpkg token create --name new-name ...` (a *new* identity)
2. Re-add the new name to every organization (`/v1/orgs/{slug}/members`
   is keyed by token **name**)
3. Update every stored copy (CI secrets, requirement files)
4. `cvcpkg token revoke --name old-name`

Four steps, two of which are easy to forget — miss step 2 and your CI
publishes start failing with `403 you are not a member of organization`.

`cvcpkg token rotate` collapses this to one step. The token row itself
survives; only the secret changes.

## How it works

```
        POST /v1/tokens/{name}/rotate   {"grace_minutes": 60}
                          │
              ┌───────────▼───────────┐
              │ current secret hash   │──► becomes previous_token_hash,
              │                       │    valid until now + 60 min
              │ fresh secret          │──► new current hash; raw value
              │                       │    returned ONCE in the response
              └───────────────────────┘
```

- **Grace window** (`grace_minutes`, 0–10080, default 0): the old
  secret keeps authenticating until the window closes, so you can swap
  stored copies calmly instead of racing an outage. `0` kills the old
  secret immediately.
- The window is **clamped to the token's own expiry** — an old secret
  never outlives its token.
- **Revocation wins**: `cvcpkg token revoke` kills *both* secrets at
  once, grace window or not.
- Each rotation is recorded in the audit log as `token_rotate`
  (actor, target, grace_minutes).

### Security properties

- **Who may rotate**: an admin token can rotate any token; a non-admin
  token can rotate only itself.
- **A grace-window (old) secret is data-plane only.** During the window
  the old secret keeps working for the credential's normal job —
  publishing and uploading — because that is the whole reason the grace
  window exists (an in-flight CI job keeps working while the secret is
  swapped). But it is refused (`403`) at every *control-plane* endpoint:
  it cannot rotate, create or revoke tokens, edit a token's
  email/profile, or manage organization membership. Those operations
  can establish access that *outlives* the grace window (a fresh
  permanent token, a new org membership), so a leaked old secret must
  not reach them — otherwise rotation would not actually remediate the
  leak.
- **For a suspected leak, use `--grace-minutes 0` (the default).** With
  a grace window the old secret retains data-plane powers (it can still
  publish/yank) until the window closes; `0` kills it immediately.
  Reserve `grace_minutes > 0` for *uncompromised* rotations where you
  only need a swap window.
- **Revocation wins**: `cvcpkg token revoke` kills both the current and
  grace secrets at once.
- Expired or revoked tokens cannot be rotated (`404`) — a rotated
  secret for a dead token would be dead on arrival.
- The new secret is returned exactly once and only its HMAC-SHA256
  hash is stored, same as `token create`.

## Example: routine CI credential rotation

The common case — rotating a publisher token used by GitHub Actions,
with a one-hour overlap so in-flight jobs keep working:

```bash
export CVCPKG_SERVER_URL=https://cvcpkg.org
export CVCPKG_TOKEN=cvctok_<current-secret-or-admin>

cvcpkg token rotate --name ci-publisher --grace-minutes 60
# Rotated token 'ci-publisher' (role: publisher)
#   New token: cvctok_AbC...xyz
#   ⚠ Store this token securely — it will not be shown again.
#   Old secret valid until: 2026-07-16T19:30:00+00:00

# Swap the stored copy while both secrets work:
gh secret set CVCPKG_TOKEN --repo my-org/my-repo --body 'cvctok_AbC...xyz'

# Nothing else to do: org memberships, role, and expiry are untouched.
# After the hour, the old secret is dead.
```

Suspected leak? Skip the grace window:

```bash
cvcpkg token rotate --name ci-publisher
#   Old secret is no longer valid.
```

Or over the raw API:

```bash
curl -X POST "$CVCPKG_SERVER_URL/v1/tokens/ci-publisher/rotate" \
  -H "Authorization: Bearer $CVCPKG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"grace_minutes": 60}'
```

Response:

```json
{
  "name": "ci-publisher",
  "role": "publisher",
  "token": "cvctok_...",
  "expires_at": "2027-06-05T00:00:00+00:00",
  "previous_valid_until": "2026-07-16T19:30:00+00:00"
}
```

## Operational notes

- **Self-rotation needs no admin**: set `CVCPKG_TOKEN` to the token
  being rotated. This lets each credential holder rotate on their own
  schedule without sharing admin access.
- **DB deployments** need Alembic migration `015`
  (`cvcpkg-server migrate upgrade head`) before the endpoint is used;
  fresh installs and YAML-backend servers need nothing.
- The audit trail (`GET /v1/audit`, admin-only) is the place to check
  if you suspect a rotation you didn't perform.

See also: [API reference — Token Management](api-reference.md#token-management),
[Organizations](organizations.md) (membership is keyed by token name and
survives rotation).
