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

### 4.3 Automatic Garbage Collection

The server can run periodic GC (via cron or a background task):

- **Max age:** Delete cache entries older than N days (configurable,
  default 30).
- **Max count per key:** Keep only the M most recent entries per
  `(name, platform, arch, build_type, link)` tuple.
- **Storage cap:** If total cache storage exceeds a threshold, evict
  LRU entries.

For the local cache, `cvcpkg cache purge --max-size 10G` serves the
same purpose and can be added to shell profiles or CI setup steps.

### 4.4 Cache Integrity

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

## 6. Implementation Phases

### Phase 1: Local Build Cache (MVP)

- Extend `chain_hash()` to include `_common/` scripts.
- Add `BuildCache` class with `lookup()`, `store()`, `evict()`.
- Wire cache into `build_all()` with `--force-clean` / `--no-cache`
  flags.
- Add `cvcpkg cache` subcommand group (`list`, `info`, `remove`,
  `purge`).
- Tests: cache hit/miss, invalidation on recipe change, `--force-clean`
  bypass.

### Phase 2: Server-Side Cache Lookup

- Add `recipe_version` filter to `GET /v1/packages`.
- Add `GET /v1/cache/status` lightweight probe endpoint.
- Wire `--server-cache` into `build_all()` flow.
- Download + extract + local cache population from server hits.
- Tests: mock server cache hit, fallback to build on miss.

### Phase 3: Server-Side Cache Push

- Wire `--server-cache-push` to publish non-release packages after
  build.
- CI workflow integration: enable push on the first shard, lookup on
  subsequent shards.
- Tests: publish after build, dedup against existing entries.

### Phase 4: Cache Management & GC

- `cvcpkg cache` commands for server-side (`--server` flag).
- Server API endpoints (`/v1/cache`, `/v1/cache/stats`).
- Automatic GC task on the server.
- `purge --stale` with chain hash recomputation.
- Storage accounting and LRU eviction.

---

## 7. Expected Impact

| Scenario | Before | After |
|---|---|---|
| CI push touching 1 recipe | ~2h full rebuild | ~5min (1 build + cache hits) |
| CI push touching 0 recipes (docs, CI config) | ~2h full rebuild | ~2min (all cache hits) |
| Local dev: re-run pack-all after editing one recipe | ~2h | ~5min |
| New CI shard downloading deps from another shard | Build from source | Download cached artifacts |
| Release build (--force-clean) | Full rebuild | Full rebuild (unchanged, as intended) |
