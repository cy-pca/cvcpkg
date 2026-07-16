# Organizations

Organizations let teams share a namespace for publishing, managing, and
installing packages together. An organization groups packages under a common
slug (e.g. `cvc/zlib`) and controls who can publish to that namespace.

---

## Concepts

| Term | Meaning |
|---|---|
| **Slug** | URL-safe identifier for the org (e.g. `cvc`). Must start with a letter or underscore, followed by alphanumeric characters, underscores, or hyphens. Consecutive hyphens are not allowed. |
| **Owner** | A member who can add/remove members, update org settings, and upload logos. The org creator is automatically an owner. |
| **Member** | A user who can publish packages under the org namespace. |
| **Private org** | An organization whose packages and member list are only visible to its members (and server admins). |
| **Storage limit** | Maximum total size of all packages published under the org. Default: 10 GiB. Configurable by admins via the `CVCPKG_ORG_STORAGE_LIMIT_BYTES` environment variable. |

---

## Creating an Organization

Any user with a `publisher` or `admin` token can create an organization:

```bash
curl -X POST https://cvcpkg.org/v1/orgs \
  -H "Authorization: Bearer $CVCPKG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "slug": "my-team",
    "display_name": "My Team",
    "description": "Shared packages for our group",
    "is_private": false
  }'
```

The creator automatically becomes an **owner**.

### Response

```json
{
  "slug": "my-team",
  "display_name": "My Team",
  "description": "Shared packages for our group",
  "logo_url": "",
  "homepage": "",
  "is_private": false,
  "storage_limit_bytes": 10737418240,
  "storage_used_bytes": 0,
  "created_at": "2026-05-25T12:00:00+00:00",
  "created_by": "alice"
}
```

---

## Managing Members

### List Members

```bash
cvcpkg org members my-team
```

### Add a Member

Org owners (or server admins) can add members:

```bash
cvcpkg org add-member my-team alice
cvcpkg org add-member my-team bob --role owner
```

Roles:

| Role | Publish | Manage members | Update settings |
|---|---|---|---|
| `member` | ✅ | ❌ | ❌ |
| `owner` | ✅ | ✅ | ✅ |

### Remove a Member

```bash
cvcpkg org remove-member my-team bob
```

The member's personal API token remains valid — they simply lose access to
the org namespace.

---

## Publishing to an Organization

Pass `--org` when publishing a package to place it under the org namespace:

```bash
cvcpkg publish my-lib --org my-team
```

The server validates:

1. The org exists.
2. Your token belongs to a member of the org.
3. The org has enough storage remaining for the upload.

If any check fails, the publish is rejected (403 or 413).

### Pushing Recipes

Recipes can also be scoped to an org:

```bash
cvcpkg recipes push my-lib --org my-team
```

### Remote Builds

When submitting remote build jobs, pass `--org` to publish the results under
the org namespace:

```bash
cvcpkg builds submit my-lib --org my-team
```

---

## Installing Organization Packages

Organization packages appear in the catalog with the `org/name` format.
Install them the same way as any other package:

```bash
cvcpkg install my-lib --org my-team --prefix ./deps
```

Or search for packages within an org:

```bash
cvcpkg search --org my-team
cvcpkg search my-lib --org my-team
```

### Private Organization Packages

If the org is private, you must authenticate to access its packages:

```bash
export CVCPKG_TOKEN="cvctok_..."
cvcpkg install my-lib --org my-team --prefix ./deps
```

Anonymous users cannot see or install packages from private organizations.

---

## Updating Organization Settings

Org owners can update the display name, description, homepage, and privacy
setting:

```bash
curl -X PATCH https://cvcpkg.org/v1/orgs/my-team \
  -H "Authorization: Bearer $CVCPKG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "display_name": "My Team (Updated)",
    "description": "New description",
    "is_private": true
  }'
```

Only server admins can change `storage_limit_bytes`.

---

## Organization Logos

Upload a logo (PNG, JPEG, SVG, or WebP, max 512 KB):

```bash
curl -X POST https://cvcpkg.org/v1/orgs/my-team/logo \
  -H "Authorization: Bearer $CVCPKG_TOKEN" \
  -F "file=@logo.png"
```

The logo is served at:

```
GET https://cvcpkg.org/v1/orgs/my-team/logo
```

---

## Private Organizations

Setting `is_private` to `true` restricts visibility:

- **Package catalog**: Private org packages are only visible to members and
  admins. Anonymous users and non-members see no results.
- **Org detail page**: Returns `404` to non-members instead of revealing the
  org exists.
- **Member list**: Only visible to members and admins.
- **Package downloads**: Require a valid token belonging to an org member.

The visibility rule is enforced on **every** read path — the per-name lookup,
the paged listing, search (including its facet buckets and counts), and the
catalog — so a private org's packages, and even their existence, never leak to a
non-member.

> On an **edge/satellite** server, org packages are *local-only*: published on
> the edge and never propagated to or from the upstream primary. See
> [Clusters & Federation](clusters-and-federation.md) for the upstream-mirror +
> local-only-org model, and for depending on another server's org packages
> across a federation.

To make an existing org private:

```bash
curl -X PATCH https://cvcpkg.org/v1/orgs/my-team \
  -H "Authorization: Bearer $CVCPKG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"is_private": true}'
```

---

## Storage Limits

Each org has an independent storage budget (default 10 GiB). The server
tracks cumulative storage as packages are published and deleted.

If a publish would exceed the limit, the server returns `413 Payload Too Large`.

**Visibility:** an org's storage limit and usage are shown only to its
**members** and **super-admins**. For everyone else the API returns them as
`null` (in `GET /v1/orgs` and `GET /v1/orgs/{slug}`) and the web UI simply omits
the storage figures — so a public org's budget is never exposed to outsiders.

Admins can adjust the limit per org:

```bash
curl -X PATCH https://cvcpkg.org/v1/orgs/my-team \
  -H "Authorization: Bearer $CVCPKG_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"storage_limit_bytes": 21474836480}'
```

The default limit is configurable server-wide via the
`CVCPKG_ORG_STORAGE_LIMIT_BYTES` environment variable.

---

## Permissions Reference

| Operation | Required role |
|---|---|
| Create org | `publisher` or `admin` token |
| View public org | Any (unauthenticated OK) |
| View private org | Org member or `admin` |
| Update org settings | Org owner or `admin` |
| Change storage limit | `admin` only |
| Upload logo | Org owner or `admin` |
| Add/remove members | Org owner or `admin` |
| Publish to org | `publisher`+ token AND org membership |
| Install from public org | Any (unauthenticated OK) |
| Install from private org | Org member or `admin` |
