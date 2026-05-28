# Build Cache Roadmap

**Status:** Proposed  
**Author:** CVC Team  
**Date:** 2026-05-27

## Problem Statement

Today `pack-all` and `build-all` rebuild every recipe from scratch on
every invocation.  A full `pack-all --platform linux` builds 28+ C/C++
libraries sequentially, taking upwards of two hours, even if only one
recipe changed.  On CI this wastes compute across every push; locally it
makes iterative development painful.

## Design Goal

Avoid redundant rebuilds by caching build artifacts keyed on a
content-addressable hash of all inputs that affect the output.  A
`--force-clean` flag allows developers and CI release jobs to bypass the
cache when a guaranteed-clean build is required.

---

## 1. Content-Addressable Build Key

### 1.1 What Already Exists

`chain_hash()` in `builder.py` computes a transitive SHA-256 over:

- The recipe's own `recipe.yaml` content
- Every build script referenced by the build matrix
- Every patch file listed in the recipe
- Recursive chain hashes of all transitive build dependencies

This hash is already written into manifests as `recipe_sha256` but is
**never consulted before building**.  It is the natural cache key.

### 1.2 The Build Key Tuple

A build artifact is uniquely identified by:

```
(recipe_name, chain_hash, platform, arch, config, link)
```

Any change to a recipe, its scripts, its patches, or any dependency's
recipe propagates through `chain_hash`, producing a new key and a cache
miss — exactly the right invalidation behaviour.

### 1.3 Inputs Not Yet Captured by chain_hash

| Input | Current Status | Recommendation |
|---|---|---|
| **Shared env scripts** (`_common/env-wasm.ps1`, `_common/env-freebsd.sh`, etc.) | Not hashed | Hash all files in `_common/` into every recipe's chain hash |
| **Toolchain version** (GCC, Clang, MSVC, emsdk) | Not hashed | Optionally include a toolchain fingerprint via `--toolchain-tag` so CI can differentiate runner images |
| **`build.matrix[].env` overrides** | Already in `recipe.yaml` | Covered implicitly |
| **`source.sha256`** (upstream tarball) | Already in `recipe.yaml` | Covered implicitly — any change to the YAML changes the hash |

---

## 2. Local Build Cache

### 2.1 Cache Layout

```
~/.cache/cvcpkg/build-cache/
  <chain_hash>-<platform>-<arch>-<config>-<link>/
    manifest.yaml           # the generated bundle manifest
    archive.tar.zst         # the packed archive (only for assigned recipes)
    install.tar.zst         # compressed install tree snapshot
    meta.json               # bookkeeping (recipe name, built_at, builder info)
```

The cache directory follows XDG conventions and is overridable via
`CVCPKG_BUILD_CACHE_DIR`.  Set to an empty string to disable caching.

### 2.2 Build Flow with Cache

```
for recipe in topological_order:
    key = build_key(recipe, all_recipes, platform, arch, config, link)

    cached = cache.lookup(key)
    if cached and not force_clean:
        log(f"{recipe.name}: cache hit ({key.chain_hash[:12]}…)")
        # Extract cached install tree into prefix for downstream deps.
        extract_to_prefix(cached.install_tree, prefix)
        if recipe in assigned_shard:
            copy_archive(cached.archive, output_dir)
        continue

    # Cache miss — build normally.
    ctx = build_recipe(...)
    manifest = generate_manifest(...)
    archive = create_archive(...)

    # Store result in cache for next time.
    cache.store(key, ctx.install_dir, archive, manifest)
```

### 2.3 What Gets Cached

| Recipe role | Install tree | Archive |
|---|---|---|
| **Assigned** (this shard packages it) | ✓ | ✓ |
| **Dependency only** (built for the prefix) | ✓ | ✗ |

Dependency-only recipes do not need a packaged archive — only the
install tree matters for prefix merging.

### 2.4 CLI Flags

| Flag | Effect |
|---|---|
| `--force-clean` | Ignore cache entirely; build everything fresh |
| `--no-cache` | Alias for `--force-clean` |
| `--cache-dir <path>` | Override cache location (`CVCPKG_BUILD_CACHE_DIR`) |

Default behaviour (no flag): consult the cache.

---

## 3. Server-Side Build Cache

### 3.1 Motivation

The local cache helps one developer on one machine.  In CI, each runner
starts cold.  A server-side cache allows:

- One CI run to build a recipe and push the result.
- Subsequent CI runs (same or other platforms/shards) to download
  dependency artifacts instead of rebuilding them.
- Developers to pull prebuilt dependencies from the server rather than
  building locally.

The cvcpkg-server already stores published packages with
`recipe_version` (the chain hash) in the manifest metadata.  This is
the foundation of a server-side cache.

### 3.2 Server Cache Lookup Flow

Before building a recipe, `build_all()` can optionally check whether a
matching artifact already exists on the server:

```
for recipe in topological_order:
    key = build_key(recipe, ...)

    # 1. Check local cache first (fast, no network).
    if local_cache.hit(key) and not force_clean:
        use_local(key)
        continue

    # 2. Check server cache (network, but still faster than building).
    if server_cache_enabled and not force_clean:
        remote = server.find_package(
            name=recipe.name,
            recipe_version=key.chain_hash,
            platform=platform,
            arch=arch,
            build_type=config,
            link=link,
        )
        if remote:
            log(f"{recipe.name}: server cache hit, downloading")
            archive = download(remote.archive_url)
            extract_to_prefix(archive, prefix)
            local_cache.store_from_download(key, archive)
            continue

    # 3. Cache miss everywhere — build from source.
    ctx = build_recipe(...)
    archive = create_archive(...)
    local_cache.store(key, ctx.install_dir, archive, manifest)

    # 4. Optionally push to server for other runners.
    if server_push_enabled:
        server.publish(archive, recipe_version=key.chain_hash)
```

### 3.3 Server API Additions

**Find cached build** — query by chain hash:

```
GET /v1/packages?name=zlib&recipe_version=<chain_hash>&platform=linux&arch=x86_64&build_type=release&link=shared
```

The existing `/v1/packages` endpoint already supports filtering by
`name` and `platform`.  Adding `recipe_version` filtering enables
exact-match cache lookups.  The `recipe_version` field in `PackageRow`
already stores the chain hash.

**Cache-specific metadata endpoint** (new):

```
GET /v1/cache/status?name=zlib&chain_hash=<hash>&platform=linux
```

Returns whether a matching build exists, its age, size, and download
URL without transferring the full package list.  Lightweight for
pre-build probes.

**Cache push** — reuse existing publish:

```
POST /v1/publish
  ?name=zlib&version=1.3.1+cvc.1&platform=linux&...
  &recipe_version=<chain_hash>
  &release_tag=          # empty = not part of a release, just a cache entry
```

Published with an empty `release_tag`, these packages serve as cache
entries.  They can be garbage-collected independently of official
releases.

### 3.4 Server Cache CLI Flags

| Flag | Effect |
|---|---|
| `--server-cache` | Enable server-side cache lookup during builds.  Requires `CVCPKG_SERVER_URL` and `CVCPKG_TOKEN`. |
| `--server-cache-push` | After building, publish artifacts to the server for other runners.  Implies `--server-cache`. |
| `--no-server-cache` | Disable server-side lookups even if configured. |

Environment variables `CVCPKG_SERVER_CACHE=1` and
`CVCPKG_SERVER_CACHE_PUSH=1` provide non-interactive equivalents for CI
workflow files.

### 3.5 Server Cache Storage Backend

The server-side build cache must reuse the same pluggable storage
backend infrastructure already in place for published packages.  This
means cache archives can live on any backend the server is configured
to use, not just local disk.

**Supported backends** (same as package storage):

| Backend | URI scheme | Notes |
|---|---|---|
| Local filesystem | `file://` | Default.  Cache stored under `<state_dir>/cache/` |
| Amazon S3 | `s3://bucket/prefix` | Via `S3Backend` or `S3CliBackend` |
| Azure Blob Storage | `az://container/prefix` | Via `AzureBlobBackend` |
| SFTP | `sftp://host/path` | Via `SftpBackend` |
| Rclone | `rclone://remote:path` | Via `RcloneBackend` (supports 40+ providers) |

**Configuration:**

```
# Default: same backend as packages, under a /cache/ prefix.
CVCPKG_CACHE_STORAGE_URI=s3://my-bucket/cvcpkg-cache/

# If not set, falls back to the package storage URI with a /cache/
# subdirectory appended.
```

**Design principle:** The `StorageBackend` protocol already defines
`upload()`, `download()`, `delete()`, and `list_objects()` — exactly
the operations the server-side cache needs.  The cache layer calls
the same backend instance (or a separate instance pointed at a
different prefix/bucket) rather than implementing its own I/O.

This ensures cache storage inherits all existing backend features:
retry logic, streaming uploads, multipart support, authentication,
and provider-specific optimizations.

---

## 4. Cache Management

Both the local and server-side caches need operations for inspection,
maintenance, and cleanup.

### 4.1 CLI Commands

```
cvcpkg cache                          # overview of cache location, size, entry count
cvcpkg cache list                     # list cached builds
cvcpkg cache list --recipe zlib       # filter by recipe name
cvcpkg cache list --platform linux    # filter by platform
cvcpkg cache list --stale             # entries whose chain_hash no longer matches
                                      # the current recipe (recipe has changed)
cvcpkg cache info <chain_hash_prefix> # show details of a specific cache entry
cvcpkg cache remove <chain_hash_prefix>  # remove a specific entry
cvcpkg cache purge                    # remove all entries
cvcpkg cache purge --stale            # remove only entries that no longer match
                                      # any current recipe chain_hash
cvcpkg cache purge --older-than 30d   # remove entries older than 30 days
cvcpkg cache purge --max-size 10G     # evict oldest entries until cache is under 10 GB
```

### 4.2 Server-Side Cache Management

Server-side cache entries are packages published with an empty
`release_tag`.  Management uses existing server infrastructure plus
targeted extensions.

**CLI commands** (require admin or publisher token):

```
cvcpkg cache list --server                    # list server cache entries (non-release packages)
cvcpkg cache list --server --recipe zlib      # filter
cvcpkg cache purge --server                   # purge all non-release cache entries
cvcpkg cache purge --server --stale           # purge entries whose chain_hash doesn't
                                              # match any current recipe
cvcpkg cache purge --server --older-than 14d  # purge entries older than 14 days
cvcpkg cache purge --server --keep-latest 3   # keep only the 3 most recent cache entries
                                              # per recipe+platform tuple
```

**Server API additions:**

```
GET  /v1/cache                # list cache entries (non-release packages)
     ?name=...&platform=...   # optional filters
     &older_than=14d          # optional age filter

DELETE /v1/cache              # bulk purge (admin only)
       ?older_than=14d        # optional filters
       &stale=true            # only entries not matching current recipes

GET  /v1/cache/stats          # aggregate stats: total size, entry count,
                              # oldest entry, per-platform breakdown
```

These endpoints are thin wrappers over the existing `packages` table,
filtered to `release_tag = ""` and `recipe_version != ""`.

### 4.3 Access Control & Organization Caches

Server-side cache entries inherit the same ACL rules as published
packages.  Private organization caches must never be publicly
accessible.

#### 4.3.1 Organization Cache Isolation

Each organization has its own logical cache namespace.  Cache entries
published under an org are scoped to that org:

- **Lookup:** When `--org myorg` is active, cache lookups include
  `org_slug=myorg` in the query.  An org build never receives cache
  hits from another org or from the global/public cache, and vice
  versa.
- **Push:** Cache entries published with `--org myorg` are tagged with
  `org_slug` and are only visible to authenticated members of that
  org.
- **Base packages:** Public/base recipe cache entries (no org) are
  available to everyone, including org builds, since base dependencies
  are shared.

#### 4.3.2 Private Organization Enforcement

Private organizations (where `OrgInfo.is_private = True`) have stricter
rules:

- **All cache endpoints** (`GET /v1/cache`, `GET /v1/cache/status`,
  `GET /v1/download/...`) require authentication and verify org
  membership before returning results.
- **Cache listing** with `--server --org myorg` returns 403 for
  non-members.
- **Download** of a cache archive belonging to a private org requires
  a valid token from an org member.

This reuses the existing `_db_orgs.is_member()` check already applied
in the publish endpoint.

#### 4.3.3 Per-Organization Cache Storage Limits

Each organization has a configurable maximum cache storage size,
independent of the org's package storage limit:

| Setting | Scope | Default | Who can change |
|---|---|---|---|
| `cache_storage_limit_bytes` | Per org | 10 GB | Global admin |
| `global_cache_storage_limit_bytes` | Entire server | 100 GB | Global admin |

- **Per-org limit:** Tracked separately from the existing
  `storage_limit_bytes` (which covers released packages).  When an org
  cache push would exceed the limit, the server returns 413 and the
  build continues without caching.
- **Global limit:** Caps total cache storage across all orgs and the
  public cache.  When exceeded, the server GC evicts LRU entries across
  all namespaces.
- **Admin management:**

```
# Set per-org cache limit
PATCH /v1/orgs/{slug}
  {"cache_storage_limit_bytes": 21474836480}   # 20 GB

# Set global cache limit (admin only)
PATCH /v1/admin/settings
  {"global_cache_storage_limit_bytes": 107374182400}   # 100 GB
```

CLI equivalents:

```
cvcpkg admin org update myorg --cache-storage-limit 20G
cvcpkg admin settings --global-cache-limit 100G
```

#### 4.3.4 Cache Storage Accounting

The server tracks cache usage per org:

```
GET /v1/cache/stats                   # global cache stats (admin)
GET /v1/cache/stats?org=myorg         # org-specific cache stats (org member or admin)
```

Response includes:

```json
{
  "org": "myorg",
  "cache_entries": 142,
  "cache_size_bytes": 5368709120,
  "cache_storage_limit_bytes": 10737418240,
  "cache_utilization_pct": 50.0,
  "oldest_entry": "2026-05-15T10:00:00Z",
  "newest_entry": "2026-05-27T14:00:00Z"
}
```

### 4.4 Automatic Garbage Collection

The server can run periodic GC (via cron or a background task):

- **Max age:** Delete cache entries older than N days (configurable,
  default 30).
- **Max count per key:** Keep only the M most recent entries per
  `(name, platform, arch, build_type, link)` tuple.
- **Storage cap:** If total cache storage exceeds a threshold, evict
  LRU entries.

For the local cache, `cvcpkg cache purge --max-size 10G` serves the
same purpose and can be added to shell profiles or CI setup steps.

### 4.5 Cache Integrity

On retrieval, both local and server cache entries are verified:

- **SHA-256 check:** The archive hash stored in `meta.json` (local) or
  `PackageRow.sha256` (server) is verified against the downloaded file.
- **Chain hash re-check:** Optionally, recompute `chain_hash` from
  current recipes and compare against the stored `recipe_version`.  If
  they diverge, the entry is stale — warn and rebuild.
- **Signature verification:** Server cache entries can be signed with
  the same Ed25519 mechanism used for releases.

---

## 5. Cache Invalidation Summary

| What changed | Effect |
|---|---|
| `recipe.yaml` content | chain_hash changes → cache miss |
| Build script content | chain_hash changes → cache miss |
| Patch file content | chain_hash changes → cache miss |
| Dependency recipe change | Dependency chain_hash changes → all downstream hashes change → cascade of misses |
| Shared `_common/` script | chain_hash changes (with proposed enhancement) → cache miss |
| Upstream tarball re-release | `source.sha256` in recipe.yaml changes → cache miss |
| Toolchain upgrade | `--toolchain-tag` changes → cache miss (opt-in) |
| Platform/arch/config/link change | Different key → different cache slot |

No explicit invalidation commands are needed for correctness — the
content-addressable nature of `chain_hash` handles it.  The
`purge --stale` commands exist purely for disk/storage reclamation.

---

## 6. Test Plan

Comprehensive tests are required at every phase.  Each feature must
ship with both unit tests and integration tests before it can be
considered complete.

### 6.1 Unit Tests

| Area | Tests |
|---|---|
| **BuildCache core** | `test_cache_lookup_hit` — pre-populated cache dir returns match |
| | `test_cache_lookup_miss` — empty cache returns None |
| | `test_cache_store` — stores archive + meta.json, verifies contents |
| | `test_cache_evict` — removes entry, subsequent lookup returns None |
| | `test_cache_purge_max_size` — evicts LRU entries until size ≤ limit |
| | `test_cache_purge_stale` — removes entries whose chain_hash no longer matches current recipes |
| **chain_hash** | `test_chain_hash_includes_common_scripts` — modifying a `_common/` file changes chain_hash |
| | `test_chain_hash_stable` — same inputs produce same hash across runs |
| | `test_chain_hash_cascade` — changing a dependency recipe changes all downstream hashes |
| **Cache key dimensions** | `test_cache_key_platform_separation` — different platforms → different cache slots |
| | `test_cache_key_arch_separation` — different architectures → different cache slots |
| | `test_cache_key_build_type_separation` — Debug vs Release → different cache slots |
| | `test_cache_key_link_separation` — static vs shared → different cache slots |
| **CLI flags** | `test_no_cache_flag` — `--no-cache` bypasses lookup and store |
| | `test_force_clean_flag` — `--force-clean` bypasses cache lookup |
| | `test_cache_list` — lists entries with correct metadata |
| | `test_cache_info` — shows detailed entry info |
| | `test_cache_remove` — removes specified entries |
| **Server models** | `test_cache_storage_limit_field` — OrgInfo has `cache_storage_limit_bytes` |
| | `test_global_cache_limit_setting` — ServerSettings has `global_cache_storage_limit_bytes` |
| | `test_cache_stats_response_model` — CacheStatsResponse serializes correctly |
| **ACL — org isolation** | `test_cache_lookup_scoped_to_org` — org cache lookup only returns entries for that org |
| | `test_cache_push_tagged_with_org` — cache push includes org_slug |
| | `test_base_cache_visible_to_all` — public/base cache entries are accessible to org builds |
| | `test_cross_org_cache_invisible` — org A cannot see org B's cache entries |
| **ACL — private orgs** | `test_private_org_cache_requires_auth` — unauthenticated requests return 401 |
| | `test_private_org_cache_requires_membership` — authenticated non-member returns 403 |
| | `test_private_org_cache_member_access` — authenticated member returns 200 |
| | `test_private_org_cache_download_requires_membership` — download endpoint checks membership |
| **Admin controls** | `test_admin_set_per_org_cache_limit` — PATCH /v1/orgs/{slug} updates cache limit |
| | `test_admin_set_global_cache_limit` — PATCH /v1/admin/settings updates global limit |
| | `test_non_admin_cannot_set_cache_limit` — non-admin PATCH returns 403 |
| | `test_cache_push_over_org_limit_returns_413` — push exceeding per-org limit returns 413 |
| | `test_cache_push_over_global_limit_returns_413` — push exceeding global limit returns 413 |
| **Cache integrity** | `test_sha256_verification_on_retrieve` — corrupted archive detected |
| | `test_chain_hash_recheck_on_retrieve` — stale entry flagged |
| **GC** | `test_gc_max_age` — entries older than max_age are evicted |
| | `test_gc_storage_cap` — LRU eviction when storage exceeds cap |
| | `test_gc_respects_org_boundaries` — GC accounts for per-org limits |

### 6.2 Integration Tests

| Area | Tests |
|---|---|
| **Local cache end-to-end** | `test_build_populates_cache` — `pack-all` stores result in cache |
| | `test_rebuild_uses_cache` — second `pack-all` skips build, uses cache |
| | `test_recipe_change_invalidates_cache` — modify recipe → rebuild on next `pack-all` |
| | `test_dep_change_cascades` — modify dependency recipe → downstream packages rebuilt |
| **Server cache end-to-end** | `test_server_cache_push_and_pull` — build on machine A, push to server; machine B pulls from server cache |
| | `test_server_cache_fallback` — server miss gracefully falls back to local build |
| **Org cache end-to-end** | `test_org_build_uses_org_cache` — org build stores and retrieves from org-scoped cache |
| | `test_org_build_uses_base_cache` — org build retrieves base dependency from public cache |
| | `test_private_org_cache_end_to_end` — create private org → push cache → verify non-member gets 403 → member gets 200 |
| | `test_org_cache_limit_enforced` — push entries until limit → next push returns 413 → build still succeeds (just uncached) |
| **Admin end-to-end** | `test_admin_adjusts_org_limit_and_push_succeeds` — admin raises limit → push that was previously 413 now succeeds |
| | `test_admin_global_limit_triggers_gc` — admin lowers global limit → GC reclaims space |
| **CLI end-to-end** | `test_cache_list_shows_entries` — after build, `cvcpkg cache list` shows entries |
| | `test_cache_remove_deletes_entry` — `cvcpkg cache remove` deletes entry, confirmed by list |
| | `test_cache_purge_reclaims_space` — `cvcpkg cache purge --max-size 0` removes all entries |
| | `test_cache_stats_server` — `cvcpkg cache stats --server` shows utilization |

### 6.3 Test Infrastructure

- **Fixtures:** Reuse existing `tmp_recipe_dir`, `mock_server`, and
  `test_client` fixtures.  Add `build_cache_dir` fixture backed by
  `tmp_path`.
- **Server tests:** Use FastAPI `TestClient` (already in place) for
  cache endpoint integration tests.  Add `test_admin_client` fixture
  with admin token.
- **Org fixtures:** Add `private_org` and `public_org` fixtures that
  create orgs via the API and return (org, member_token,
  non_member_token) tuples.
- **Coverage:** All cache code paths must have ≥ 90% line coverage.
  Run `pytest --cov=cvcpkg.builder --cov=cvcpkg.server` to verify.

---

## 7. Implementation Phases

### Phase 1: Local Build Cache (MVP)

- Extend `chain_hash()` to include `_common/` scripts.
- Add `BuildCache` class with `lookup()`, `store()`, `evict()`.
- Wire cache into `build_all()` with `--force-clean` / `--no-cache`
  flags.
- Add `cvcpkg cache` subcommand group (`list`, `info`, `remove`,
  `purge`).
- **Unit tests:** cache hit/miss, invalidation on recipe change,
  `--force-clean` bypass, chain_hash stability/cascade,
  cache key dimensions.
- **Integration tests:** build populates cache, rebuild uses cache,
  recipe change invalidates cache, dep change cascades.

### Phase 2: Server-Side Cache Lookup + ACL

- Add `recipe_version` filter to `GET /v1/packages`.
- Add `GET /v1/cache/status` lightweight probe endpoint.
- **Storage backend:** Server-side cache uses the same pluggable
  `StorageBackend` as package storage (filesystem, S3, Azure, SFTP,
  Rclone).  Default to filesystem under `<state_dir>/cache/`.
  Configurable via `CVCPKG_CACHE_STORAGE_URI`.
- Wire `--server-cache` into `build_all()` flow.
- Download + extract + local cache population from server hits.
- **Org cache isolation:** Scope all server cache lookups by org_slug.
  Base package cache entries remain globally accessible.
- **Private org ACL:** Gate cache endpoints behind authentication +
  membership check for private orgs.
- **Unit tests:** mock server cache hit/miss, org-scoped lookup,
  private org auth/membership checks, cross-org isolation,
  storage backend integration.
- **Integration tests:** server cache push/pull, fallback to build,
  private org end-to-end.

### Phase 3: Server-Side Cache Push + Storage Limits

- Wire `--server-cache-push` to publish non-release packages after
  build.
- CI workflow integration: enable push on the first shard, lookup on
  subsequent shards.
- **Per-org cache limits:** Add `cache_storage_limit_bytes` to org
  model.  Enforce on push — return 413 when limit exceeded.
- **Global cache limit:** Add `global_cache_storage_limit_bytes` to
  server settings.  Enforce across all namespaces.
- **Admin endpoints:** `PATCH /v1/orgs/{slug}` for per-org limit,
  `PATCH /v1/admin/settings` for global limit.
- **Unit tests:** publish after build, dedup, per-org limit 413,
  global limit 413, admin set limits, non-admin 403.
- **Integration tests:** org cache limit enforced end-to-end, admin
  adjusts limit and push succeeds.

### Phase 4: Cache Management, GC & Stats

- `cvcpkg cache` commands for server-side (`--server` flag).
- Server API endpoints (`/v1/cache`, `/v1/cache/stats`).
- Automatic GC task on the server — max age, storage cap, LRU eviction.
- GC respects per-org limits and org boundaries.
- `purge --stale` with chain hash recomputation.
- Storage accounting per org and globally.
- **Unit tests:** GC max age, GC storage cap, GC org boundaries,
  SHA-256 verification, chain hash recheck.
- **Integration tests:** CLI cache list/remove/purge, cache stats
  server, admin global limit triggers GC.

---

## 8. Expected Impact

| Scenario | Before | After |
|---|---|---|
| CI push touching 1 recipe | ~2h full rebuild | ~5min (1 build + cache hits) |
| CI push touching 0 recipes (docs, CI config) | ~2h full rebuild | ~2min (all cache hits) |
| Local dev: re-run pack-all after editing one recipe | ~2h | ~5min |
| New CI shard downloading deps from another shard | Build from source | Download cached artifacts |
| Release build (--force-clean) | Full rebuild | Full rebuild (unchanged, as intended) |
