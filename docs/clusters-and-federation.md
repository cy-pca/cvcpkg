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
| **Edge / Satellite** (`CVCPKG_POPULATE_UPSTREAM` set) | **populated from an upstream primary — upstream is the default source** | **local + private-capable, never propagate** | **org + public (public warns it diverges from upstream; `CVCPKG_EDGE_STRICT_PUBLIC=1` makes public read-only)** |

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

### Mirror size budget & usage-based eviction

An edge can also cap the **total** size of its upstream-mirrored cache:

- **`CVCPKG_POPULATE_MAX_MIRROR_BYTES`** — a byte budget for populate-origin
  public packages (`0` = unbounded).

After each sync, if the mirror exceeds the budget, cvcpkg **evicts the
least-downloaded** populate-origin packages (usage comes from the Phase 2
download analytics; ties broken by largest-first) until it fits.  An evicted
package **re-populates on demand** the next time it is still upstream and
wanted.  Only populate-origin public packages are eligible — **org-local and
locally-published packages are never evicted**.  Eviction is audit-logged
(`actor = mirror-evict`), and `populate_stats` reports `last_evicted` /
`evicted_total`.

`GET /healthz` reports `populate_upstream` and `populate_stats`.

### Local public publishes diverge from upstream (warn, don't reject)

Upstream is the *default* source of truth for the public namespace, but an edge
server is read-write: a local publish into the public namespace is **accepted**
and carries a **divergence warning**. The local build shadows the canonical
upstream package of the same coordinates, takes local precedence, and the
populate loop never overwrites it:

```
$ cvcpkg publish zlib ...            # no org -> public
  published: sha256=...
  warning: publishing to the public namespace on a cluster that mirrors
     https://cvcpkg.org: this local build diverges from and shadows the canonical
     upstream package. It takes local precedence and the populate loop will not
     overwrite it. Publish into an organization (--org) if you did not intend to
     shadow upstream.
```

This is what lets a build-and-mirror cluster (e.g. the dev cluster) build its own
packages — including a multi-recipe DAG whose dependencies are also freshly
built — while still tracking upstream for everything it did **not** build locally.

**Strict mode.** Set `CVCPKG_EDGE_STRICT_PUBLIC=1` to make the public namespace
read-only again: a local public publish is then rejected with HTTP 409
(`...the public namespace is read-only. Publish into an organization instead`).
Use this when the edge must be a pure mirror of upstream.

> Internally, the populate diff is scoped to the public namespace and skips any
> variant that already exists locally, so a local public build is never clobbered
> by a later sync — and a private org package can never shadow a public upstream
> package that happens to share its name/version.

#### Concurrency — a local build racing a mirror import

A natural worry on a cluster that both **builds** packages and **mirrors** an
upstream: what if the populate loop imports a variant from upstream at the same
moment a local builder is finishing a build of that same variant — who wins,
the build or the mirror?

Variants are **immutable and first-committer-wins**. A published
`(name, version, platform, arch, config, link)` tuple is never overwritten:
`add_package` raises on a duplicate, which the API surfaces as `409`. The two
writers that can target one variant — a **builder publishing a freshly-built
package** and the **populate loop importing that variant from upstream** —
therefore resolve deterministically, and *nothing is ever clobbered*:

- **On an edge, a public local publish and a mirror import are
  first-committer-wins.** Both target the same immutable
  `(name, version, platform, arch, config, link)` tuple; whichever commits first
  wins and the other is a no-op (the populate loop skips a variant that already
  exists locally; a racing publisher gets a `409` duplicate). A local build that
  lands first therefore keeps precedence and is never overwritten by a later
  upstream import. This is the dev cluster case
  (`CVCPKG_POPULATE_UPSTREAM=https://cvcpkg.org`): a dev builder **can** publish a
  *public* package locally (with a divergence warning), which is what lets it
  build a DAG whose deps are also freshly built. Under `CVCPKG_EDGE_STRICT_PUBLIC=1`
  the public namespace is read-only instead, so public
  packages must be published to the canonical primary (cvcpkg.org) and reach the
  dev server by mirroring.

- **Where both writers are legal** — the canonical primary, or an *org*-scoped
  variant — it is **first-committer-wins**:
  - If the **build publishes first**, the populate loop sees the variant already
    exists and **discards its download and keeps the local build** — it
    explicitly yields (*"a concurrent local publish won the race — keep
    theirs"*, `server/app.py`); it does not overwrite.
  - If **populate imports first**, the builder's later publish of the same
    variant hits the immutable-duplicate check and gets `409`; that build output
    is discarded. Builds run with `--skip-existing`, so the scheduler avoids
    even dispatching a variant already present — the `409` is only the backstop
    for the narrow window between that check and the publish.

Either way exactly one of {the build, the import} wins — whichever committed to
the catalog first — and no published variant ever diverges from what a client
would get from upstream.

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

### Top-down, root-authoritative resolution

When a client talks to a **satellite** (a nearby edge server) it resolves
**top-down**: the **root is authoritative for the public namespace**, and the
satellite is a cache.  A client configured with a distinct root:

- resolves **public** packages against the **root** catalog (authoritative
  versions + checksums) — a satellite can never present a divergent or stale
  public package as authoritative;
- resolves **organization** packages against the **local** server (they are
  local-authoritative and live only there — the inverse of public);
- **falls back to the local mirror** when the root is unreachable, so an
  offline / air-gapped satellite still resolves.

Config:

- **`CVCPKG_ROOT_URL`** — the authoritative root server (default: the
  compiled-in `cvcpkg.org`).  When it equals `CVCPKG_SERVER_URL` there is no
  separate root and resolution is unchanged.
- **`CVCPKG_ROOT_CATALOG_URL`** — override the root's catalog URL directly.

An explicit `--catalog` / `CVCPKG_CATALOG_URL` bypasses this and uses the
given catalog verbatim.  (Download *locality* — fetching a root-resolved
public archive from the nearer satellite mirror — is a separate optimization,
tracked as a follow-up.)

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
| `CVCPKG_POPULATE_MAX_MIRROR_BYTES` | edge | Total mirror-cache size budget; least-downloaded populate packages are evicted over it (0 = unbounded). |
| `CVCPKG_ROOT_URL` | client | Authoritative root server for public packages (default `cvcpkg.org`); resolution is top-down when it differs from `CVCPKG_SERVER_URL`. |
| `CVCPKG_ROOT_CATALOG_URL` | client | Override the root's catalog URL directly. |
| `CVCPKG_MIRROR_MODE` / `CVCPKG_MIRROR_UPSTREAM` | mirror | Read-only mirror of a primary. |
| `registries.yaml` / `CVCPKG_REGISTRIES` / `CVCPKG_REGISTRIES_FILE` | client | Federated registry hosts → `{url, token}` (allowlist + credentials). |
