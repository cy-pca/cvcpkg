# Build cache

`cvcpkg build-all` and `cvcpkg pack-all` keep a content-addressed local
cache of per-recipe build artifacts, so unchanged recipes are restored
from disk instead of rebuilt. An optional server-side cache lets CI
runners and teammates share artifacts across machines.

## How invalidation works

Every recipe build is keyed by its **chain hash** — a transitive
SHA-256 (`chain_hash()` in `builder.py`) covering:

- the recipe's own `recipe.yaml` content
- every build script referenced by its build matrix
- every patch file listed in the recipe
- all shared helper scripts under `recipes/_common/`
- the chain hashes of all transitive dependencies

Change any of these and the hash changes, so the next build is a cache
miss — for the recipe itself and everything downstream of it. No manual
invalidation is ever needed for correctness; the purge commands below
exist only to reclaim disk space.

The full cache key is `<chain_hash>-<platform>-<arch>-<config>-<link>`,
so debug/release, static/shared, and different platforms occupy
separate slots.

## Local cache location and layout

The cache lives in `~/.cache/cvcpkg/builds/` by default
(`$XDG_CACHE_HOME/cvcpkg/builds` if `XDG_CACHE_HOME` is set) and can be
relocated with the `CVCPKG_BUILD_CACHE` environment variable:

```
~/.cache/cvcpkg/builds/
  <chain_hash>-<platform>-<arch>-<config>-<link>/
    meta.json          # name, version, hashes, sizes, timestamps
    install.tar.gz     # tarball of the recipe's install tree
```

On every hit the archive's SHA-256 is checked against `meta.json`;
corrupted entries are deleted and rebuilt automatically.

## Build flags

Both `build-all` and `pack-all` accept:

| Flag | Environment variable | Effect |
|---|---|---|
| `--no-cache` | — | Disable the local cache entirely (no lookups, no stores). Also disables the server cache: the chain hash is only computed when the local cache is enabled, so `--no-cache --server-cache <url>` does nothing server-side either. |
| `--force-clean` | — | Skip cache lookups (rebuild from source) but still store results. Use for guaranteed-clean release builds. |
| `--server-cache <url>` | `CVCPKG_SERVER_CACHE` | Server cache URL; enables server-side lookups. |
| `--server-cache-token <t>` | `CVCPKG_SERVER_CACHE_TOKEN` | Bearer token for authenticated server cache access. |
| `--server-cache-push` | `CVCPKG_SERVER_CACHE_PUSH` | Push successful builds to the server cache. |
| `--no-server-cache` | — | Disable server cache entirely (both pull and push). |
| `--server-cache-org <slug>` | `CVCPKG_SERVER_CACHE_ORG` | Scope server cache queries to an organization. |

Lookup order per recipe: local cache first, then (if configured) the
server cache, then build from source. Server hits are downloaded,
SHA-256-verified, and stored into the local cache so the next run needs
no network. The build summary reports the number of cache hits.

```sh
# Typical CI: pull from and push to the shared cache
cvcpkg build-all --platform linux --prefix ./prefix \
    --server-cache https://cvcpkg.org --server-cache-token "$TOKEN" \
    --server-cache-push

# Release build: rebuild everything, keep the cache warm
cvcpkg pack-all --platform linux --force-clean
```

## Managing the cache

The `cvcpkg cache` command group inspects and prunes both caches. The
`--server` option (or `CVCPKG_SERVER_CACHE`) takes the server URL;
without it commands operate on the local cache.

```sh
cvcpkg cache list                        # local entries, sizes, chain hashes
cvcpkg cache list --name zlib            # filter by component
cvcpkg cache list --server https://cvcpkg.org --token "$TOKEN"
cvcpkg cache info <chain-hash-prefix>    # details for one entry (prefix ok)
cvcpkg cache remove <chain-hash>         # evict one entry (full hash)
cvcpkg cache purge --all                 # empty the local cache
cvcpkg cache purge --max-size 10G        # LRU-evict until under 10 GB
cvcpkg cache purge --max-age-days 30     # drop entries unused for 30 days
cvcpkg cache purge --stale               # drop entries whose chain_hash no
                                         # longer matches any current recipe
```

`info` and `remove` resolve the entry for the current platform and
architecture; pass `--platform`, `--config`, or `--link` to target a
different variant. `purge --stale` recomputes chain hashes for every
recipe in the current recipes tree and removes anything that no longer
matches.

Server-side equivalents:

```sh
cvcpkg cache purge --server https://cvcpkg.org --token "$TOKEN" --max-age-days 14
cvcpkg cache server-stats --server https://cvcpkg.org --token "$TOKEN"
cvcpkg cache server-gc --server https://cvcpkg.org --token "$TOKEN" --max-size 50G
```

## Server-side cache

Server cache entries are ordinary packages published (via
`POST /v1/publish`) with the chain hash as `recipe_version` and an
empty release tag — so they never appear in releases and can be
garbage-collected independently. Endpoints:

| Endpoint | Auth | Purpose |
|---|---|---|
| `GET /v1/cache/status` | none (private orgs: member token) | Lightweight pre-build probe: is a build with this name + chain hash + platform cached? Returns the download URL, SHA-256, and size on a hit. |
| `GET /v1/cache` | publisher or admin | List non-release cache entries; filters: `name`, `platform`, `arch`, paging. |
| `DELETE /v1/cache` | admin | Bulk-purge cache entries, optionally `?older_than=14d`. |
| `GET /v1/cache/stats` | publisher or admin | Totals and per-org breakdown (non-admins see only their own orgs). |
| `POST /v1/cache/gc` | admin | Garbage collection: `max_age_seconds`, `max_storage_bytes` (LRU), and/or `valid_chain_hashes` (stale-entry removal). |

Entries pushed with `--server-cache-org` are tagged with the org, and
lookups against a **private** org require a token belonging to an org
member — cache probes cannot leak private artifacts. `DELETE /v1/cache`
and `POST /v1/cache/gc` require the database backend (the YAML backend
returns `501`).
