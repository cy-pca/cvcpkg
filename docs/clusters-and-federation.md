# Clusters & Federation

cvcpkg servers can be deployed in three roles, and can **federate** so a package
on one server depends on a (public or private) org package hosted on another.
This page explains the **upstream-mirror + local-only org packages** model and
**cross-server dependency resolution**.

## Cluster roles

A server's role is defined entirely by how it treats the **public namespace**
(packages with no org, `org_slug == ""`):

| Role | Public namespace | Org packages | Publishes |
|------|------------------|--------------|-----------|
| **Primary** (e.g. `cvcpkg.org`) | canonical source of truth | — | accepts public publishes |
| **Mirror** (`--mirror-mode`) | read-only replica of a primary | — | rejects **all** publishes |
| **Edge / Satellite** (`CVCPKG_POPULATE_UPSTREAM` set) | **populated from an upstream primary — upstream is canonical** | **local + private-capable, never propagate** | **org-scoped only** |

The **edge/satellite** role is the enterprise / air-gapped shape: a read-write
server that keeps its **public** catalog in sync with a canonical upstream while
hosting its **own private organization packages locally**.

> `mirror_mode` and `populate` are *different* mechanisms. A mirror is read-only.
> An edge server is read-write and uses *populate* — it never becomes read-only.

## Populate — keeping the public catalog in sync

An edge server runs a background loop that **imports missing public packages**
from its upstream primary:

```bash
cvcpkg-server run \
  --database-url "$CVCPKG_DATABASE_URL" \
  # edge role:
  #   CVCPKG_POPULATE_UPSTREAM        upstream primary base URL
  #   CVCPKG_POPULATE_UPSTREAM_TOKEN  (optional) bearer token for the upstream
  #   CVCPKG_POPULATE_INTERVAL        seconds between syncs (default 900)
  #   CVCPKG_POPULATE_PLATFORMS       (optional) platform allowlist, "linux,windows"
  #   CVCPKG_POPULATE_INCLUDE         (optional) package-name allowlist
  #   CVCPKG_POPULATE_EXCLUDE         (optional) package-name denylist
  #   CVCPKG_POPULATE_MAX_PACKAGE_BYTES  (optional) per-package size cap
```

Populate is **pull-only** — it fetches from upstream and never pushes back — and
it **only imports public packages** (upstream org-owned bundles are skipped). It
downloads each missing archive, verifies its `sha256`, and registers it exactly
like a publish (recorded as `published_by = populate:<upstream>`).

### Selective mirroring

An edge operator rarely wants to mirror the *entire* upstream catalog — some
packages are very large.  A **mirror policy** decides, per upstream bundle,
whether to mirror it (evaluated denylist → allowlist → platform → size):

- **`CVCPKG_POPULATE_EXCLUDE`** — comma-separated package names never mirrored
  (e.g. `qt6,vtk`).  The denylist wins over the allowlist.
- **`CVCPKG_POPULATE_INCLUDE`** — comma-separated package-name allowlist; when
  set, *only* these packages are mirrored.
- **`CVCPKG_POPULATE_PLATFORMS`** — platform allowlist (as before).
- **`CVCPKG_POPULATE_MAX_PACKAGE_BYTES`** — skip any bundle larger than this
  (defaults to `CVCPKG_MAX_UPLOAD_BYTES`).

Skipped packages are simply never pulled; a client that needs one still resolves
it from the authoritative upstream root (see *Resolution*).

`GET /healthz` reports `populate_upstream` and `populate_stats`.

### Upstream is canonical for public packages

Because upstream is the source of truth for the public namespace, an edge server
**rejects local publishes into the public namespace** (HTTP 409):

```
$ cvcpkg publish zlib ...            # no org -> public
409  this cluster mirrors its public catalog from an upstream primary (...);
     the public namespace is canonical upstream and cannot be published to
     locally. Publish into an organization instead (pass --org / org=...).
```

This makes a public-vs-upstream collision impossible: public packages arrive
**only** via populate. Local publishes must target an organization.

> Internally, the populate diff is scoped to the public namespace, so a private
> org package can never shadow a public upstream package that happens to share
> its name/version.

## Local-only organization packages

Organizations are a **separate namespace** — the package identity includes the
org slug, so `acme/zlib` and public `zlib` coexist as distinct packages. On an
edge server, org packages are:

- **local-authoritative** — published on the edge, never populated from or
  pushed to upstream;
- **private-capable** — a private org's packages (and member list) are visible
  only to org members and server admins.

This is how an enterprise publishes proprietary packages on its own
infrastructure while still consuming the public catalog from upstream. Publish
to an org exactly as documented in [Organizations](organizations.md); large
packages use the chunked upload path (`cvcpkg publish` handles this
automatically), which is org-aware.

### Visibility guarantees

Private-org packages are hidden from non-members on **every** read path — the
per-name lookup (`/v1/packages/{name}`), the paged listing (`/v1/packages`),
search (`/v1/search`, including its facet buckets and counts), and the catalog
(`/v1/catalog`). An anonymous or non-member caller sees only public base
packages and public-org packages; an authenticated org member additionally sees
their own org's private packages. There is no read path on which a private org's
package — or even its existence in a facet — leaks to a non-member.

## Federation — cross-server dependencies

A dependency may reference a package on a **different** cvcpkg server, so a
package built on one edge can depend on a (public or private) org package hosted
on another.

### Declaring a federated dependency

Use the `cvc://` reference form or the structured dict form:

```yaml
depends:
  runtime:
    - zlib                                     # this server, public
    - { name: iqi-core, org: shell }           # this server, org 'shell'
    - cvc://edge-b.lab/shell/iqi-core@^1        # edge-b's 'shell' org, private
    - { name: base, org: pub, server: edge-c.lab }   # dict equivalent
```

The `server` value is a **logical host** (`edge-b.lab`), never a URL. A remote
package's own *bare* dependencies resolve on that remote; only explicit `cvc://`
references cross a server boundary.

### Registries config = allowlist + credentials + URL map

The client resolves a host through `~/.config/cvcpkg/registries.yaml`:

```yaml
registries:
  edge-b.lab:
    url: http://edge-b.lab:8420        # how to reach the host
    token: cvcp_...                    # a token that can read edge-b's orgs
  edge-c.lab:
    url: https://edge-c.lab
    token: cvcp_...
```

This one file serves three purposes:

1. **URL map** — a dependency names a stable host; the config decides the URL.
2. **Per-domain credentials** — each host has its own token; a token is only
   ever sent to its own host.
3. **Trust allowlist** — a host **not** listed here is refused. A recipe can
   therefore never steer the resolver at an arbitrary/attacker host (no SSRF);
   integrity is additionally enforced by `sha256` + signatures.

Override the file location with `$CVCPKG_REGISTRIES_FILE`, or pass the whole map
inline (YAML/JSON) via `$CVCPKG_REGISTRIES` — convenient in CI/containers.

### Resolution

The resolver walks the transitive dependency closure across registries: each
node is fetched from its host's registry using that registry's token; the
closure is returned dependency-first. Fetching a **private** cross-server
dependency requires a registries token that is a **member** of the owning org —
otherwise the package is simply invisible (the same visibility rule as local
reads), and the dependency fails to resolve. A dependency naming an
un-allowlisted host is refused outright.

## Laboratory

A runnable lab proves the whole model end-to-end (canonical upstream + three
edge servers, each with public + private orgs, and a package whose dependency
chain crosses all three):

- `lab/federation_lab.py` — in-process, three real servers, no Docker.
- `docker-compose.federation.yml` — the realistic multi-host topology.
- `tests/integration/test_federation_lab.py` — the gated CI test.

See `lab/README.md`.

## Configuration reference

| Variable | Role | Meaning |
|----------|------|---------|
| `CVCPKG_POPULATE_UPSTREAM` | edge | Upstream primary base URL; setting it makes the server an edge cluster. |
| `CVCPKG_POPULATE_UPSTREAM_TOKEN` | edge | Optional bearer token for the upstream. |
| `CVCPKG_POPULATE_INTERVAL` | edge | Seconds between populate syncs (default 900). |
| `CVCPKG_POPULATE_PLATFORMS` | edge | Optional platform allowlist for imports. |
| `CVCPKG_POPULATE_INCLUDE` | edge | Optional package-name allowlist (mirror only these). |
| `CVCPKG_POPULATE_EXCLUDE` | edge | Optional package-name denylist (never mirror these); wins over the allowlist. |
| `CVCPKG_POPULATE_MAX_PACKAGE_BYTES` | edge | Per-package size cap for mirroring (default: `CVCPKG_MAX_UPLOAD_BYTES`). |
| `CVCPKG_MIRROR_MODE` / `CVCPKG_MIRROR_UPSTREAM` | mirror | Read-only mirror of a primary. |
| `registries.yaml` / `CVCPKG_REGISTRIES` / `CVCPKG_REGISTRIES_FILE` | client | Federated registry hosts → `{url, token}` (allowlist + credentials). |
