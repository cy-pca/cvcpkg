# Revision Bump & Cascade Rebuild Roadmap

**Status:** Planning  
**Author:** Copilot + Joe  
**Date:** 2026-05-27

---

## Problem Statement

When we patch an already-published package (e.g. fix a build script
for `zlib 1.3.1+cvc.1`), we need to:

1. **Bump the CVC revision** — publish the fix as `zlib 1.3.1+cvc.2`.
2. **Cascade rebuilds** — every downstream package that transitively
   depends on `zlib` must also be rebuilt and re-published with an
   incremented revision, because its binary content has changed.

Today, `cvc_revision` is a static integer in `recipe.yaml` and must
be manually incremented.  There is no CLI command to automate the
bump, no mechanism to detect which downstream packages need rebuilds,
and no orchestration to cascade the rebuild + publish across the
dependency graph.

The `chain_hash()` function already computes a transitive content
hash covering the full dependency tree, but it is only used for
provenance recording (`meta.recipe_sha256` in the manifest) — it
does not trigger rebuilds or revision bumps.

---

## Current State

| Capability | Status | Location |
|---|---|---|
| `cvc_revision` field | Manual integer in `recipe.yaml` | `builder.py:Recipe` |
| Version format `X.Y.Z+cvc.N` | Implemented | `semver.py:Version` |
| `chain_hash()` transitive hash | Implemented (no tests) | `builder.py:chain_hash()` |
| `recipe_sha256` in manifest | Populated from `chain_hash()` | `builder.py:generate_manifest()` |
| Publish duplicate detection | 409 on exact `(name,ver,plat,arch,cfg,link)` | `server/app.py`, `db.py` |
| Dependency graph extraction | `_dep_names()`, `resolve_build_order()` | `builder.py` |
| Resolver transitive deps | Backtracking with constraint merging | `resolver.py` |
| `--revision-bump` CLI flag | **Does not exist** | — |
| Cascade rebuild command | **Does not exist** | — |
| `chain_hash` unit tests | **None** | — |

---

## Dependency Graph (libcvc components)

Packages that libcvc uses, ordered by reverse dependency depth.
A revision bump to any package cascades to everything below it.

```
Layer 0 (leaves):
  zlib, emsdk, openblas, pthreads4w, cmake, ninja

Layer 1 (depends on L0):
  boost(zlib)  openssl(emsdk)  c-ares(emsdk)  abseil(emsdk)
  gsl(emsdk)   fftw3(emsdk)   log4cplus(emsdk)  yaml(emsdk)
  libjpeg-turbo(emsdk)  xz(emsdk)  zstd(emsdk)  lerc(emsdk)
  libwebp(emsdk)  clapack(emsdk)  gmp(emsdk)

Layer 2:
  re2(abseil)  protobuf(abseil,zlib)  hdf5(zlib)
  tiff(zlib,libjpeg-turbo,xz,zstd,libwebp,lerc)
  qt6(openssl,zlib)  levmar(openblas,clapack)  mpfr(gmp)
  imagemagick(zlib,tiff)  libiimod(tiff)

Layer 3:
  grpc(protobuf,abseil,openssl,c-ares,re2,zlib)
  cgal(boost,gmp,mpfr)
  vtk(qt6,hdf5,tiff,zlib)
```

**Example cascade:** patching `zlib` requires rebuilding:
`boost, protobuf, hdf5, tiff, qt6, imagemagick, libiimod,
grpc, vtk` — 9 downstream packages across 3 layers.

---

## Design

### 1. Revision Bump Command

New CLI subcommand:

```
cvcpkg revision-bump <component> [--reason "..."] [--dry-run]
```

**Behaviour:**

1. Load `recipes/<component>/recipe.yaml`.
2. Increment `recipe.cvc_revision` by 1.
3. Write the updated `recipe.yaml` back.
4. Print the old → new version string.

This is intentionally simple — just the atomic version bump.
The cascade is a separate concern (below).

### 2. Cascade Detection

New function in `builder.py`:

```python
def cascade_set(
    target: str,
    all_recipes: dict[str, Recipe],
    platform: str = "",
) -> list[str]:
    """Return topologically-sorted list of components that
    transitively depend on *target* (inclusive)."""
```

**Algorithm:**

1. Build the **reverse dependency graph** — for each recipe, record
   which components list it as a build dependency.
2. BFS/DFS from `target` through the reverse graph to collect all
   affected components.
3. Return in topological (build) order — leaves first, dependents
   last — so they can be rebuilt bottom-up.

### 3. Cascade Bump Command

New CLI subcommand:

```
cvcpkg cascade-bump <component> [--reason "..."] [--dry-run]
```

**Behaviour:**

1. Call `cascade_set(component, all_recipes)` to get the full set
   of affected packages.
2. For each package in topological order:
   - Increment `cvc_revision` in its `recipe.yaml`.
3. Print a summary table: component, old version, new version.
4. With `--dry-run`, print the table without modifying files.

### 4. Cascade Rebuild + Publish Command

New CLI subcommand:

```
cvcpkg cascade-rebuild <component> \
    --platform linux --arch x86_64 --config release --link shared \
    --server https://pkg.tx.wtf --token $TOK \
    [--dry-run] [--keep-going]
```

**Behaviour:**

1. Call `cascade-bump` logic (bump all revisions).
2. Call `build_all()` for just the cascade set, in topological
   order, into a shared prefix.
3. Call `publish` for each built archive.
4. Commit the `recipe.yaml` changes to git (with `--no-commit`
   flag to optionally defer).

### 5. Chain Hash Validation at Publish Time

Add a server-side check: when publishing a new revision, compare
the `recipe_sha256` (chain hash) against the existing entry for the
same `(name, upstream_version)`.  If the chain hash is **identical**
to an already-published revision, warn that the rebuild may be
unnecessary (the content hasn't actually changed).

### 6. Catalog Revision Metadata

Extend `CatalogEntry` and `PackageRow` with:

| Field | Type | Purpose |
|---|---|---|
| `supersedes` | `str` | Version string this entry replaces (e.g. `"1.3.1+cvc.1"`) |
| `rebuild_reason` | `str` | Human-readable reason for the rebuild |
| `rebuild_trigger` | `str` | Component that triggered the cascade (e.g. `"zlib"`) |

This enables consumers to understand **why** a new revision was
published and trace the cascade chain.

---

## Implementation Plan

### Phase 1: Foundation (unit-testable, no server changes)

| # | Task | Files | Tests |
|---|---|---|---|
| 1.1 | Add `chain_hash()` unit tests | — | `test_builder.py` |
| 1.2 | Add `cascade_set()` function | `builder.py` | `test_builder.py` |
| 1.3 | Add `revision_bump()` helper | `builder.py` | `test_builder.py` |
| 1.4 | Add `cascade_bump()` helper | `builder.py` | `test_builder.py` |

#### 1.1 — chain_hash unit tests

Currently there are **zero tests** for `chain_hash()`.  Add tests
covering:

- Single recipe with no deps → hash is deterministic, changes when
  recipe.yaml content changes.
- Recipe with one dep → hash changes when dependency's recipe.yaml
  changes.
- Diamond dependency (A→B, A→C, B→D, C→D) → hash changes when
  leaf D changes, and is stable when unrelated recipes change.
- Cycle detection → returns `""` for the cyclic node.
- Platform-conditional deps → different platforms produce different
  hashes when platform-specific deps are present.

#### 1.2 — cascade_set()

```python
def cascade_set(
    target: str,
    all_recipes: dict[str, Recipe],
    platform: str = "",
) -> list[str]:
```

Tests:
- Leaf component (no dependents) → returns `[target]`.
- Single direct dependent → returns `[target, dependent]`.
- Transitive chain A→B→C: bumping A returns `[A, B, C]`.
- Diamond: bumping D in A→B→D, A→C→D returns `[D, B, C, A]`
  (topological order).
- Component not in graph → raises `KeyError`.
- Platform filtering → only includes deps relevant to the platform.

#### 1.3 — revision_bump()

```python
def revision_bump(
    recipe_dir: Path,
    *,
    reason: str = "",
) -> tuple[str, str]:
    """Increment cvc_revision in recipe.yaml.
    Returns (old_version, new_version)."""
```

Tests:
- Bumps `cvc_revision: 1` → `cvc_revision: 2`, version goes from
  `1.3.1+cvc.1` to `1.3.1+cvc.2`.
- Works when `cvc_revision` is missing (defaults 0 → 1).
- Preserves all other fields and YAML formatting.
- Returns correct old/new version strings.

#### 1.4 — cascade_bump()

```python
def cascade_bump(
    target: str,
    all_recipes: dict[str, Recipe],
    platform: str = "",
    reason: str = "",
    dry_run: bool = False,
) -> list[tuple[str, str, str]]:
    """Bump target + all dependents.
    Returns [(name, old_ver, new_ver), ...]."""
```

Tests:
- Bumps target and all transitive dependents.
- `dry_run=True` returns the plan without modifying files.
- Order matches topological build order.
- Idempotent — running twice bumps again (not a no-op).

### Phase 2: CLI Commands

| # | Task | Files | Tests |
|---|---|---|---|
| 2.1 | `cvcpkg revision-bump` subcommand | `cli.py` | `test_cli.py` |
| 2.2 | `cvcpkg cascade-bump` subcommand | `cli.py` | `test_cli.py` |
| 2.3 | `cvcpkg cascade-rebuild` subcommand | `cli.py` | `test_cli.py` |

#### 2.1 — `revision-bump`

```
cvcpkg revision-bump zlib --reason "fix macOS arm64 build"
```

- Calls `revision_bump()`.
- Prints `zlib: 1.3.1+cvc.1 → 1.3.1+cvc.2`.
- Exit 0 on success.

Tests:
- Bumps recipe on disk, verify file content.
- `--dry-run` prints but doesn't modify.
- Missing recipe dir → error.

#### 2.2 — `cascade-bump`

```
cvcpkg cascade-bump zlib --reason "zlib macOS fix" --dry-run
```

Output:
```
Cascade from zlib (9 packages affected):
  zlib:         1.3.1+cvc.1 → 1.3.1+cvc.2
  boost:        1.86.0+cvc.1 → 1.86.0+cvc.2
  hdf5:         1.14.5+cvc.1 → 1.14.5+cvc.2
  protobuf:     27.5+cvc.1 → 27.5+cvc.2
  tiff:         4.7.0+cvc.1 → 4.7.0+cvc.2
  qt6:          6.7.3+cvc.1 → 6.7.3+cvc.2
  imagemagick:  7.1.1+cvc.1 → 7.1.1+cvc.2
  libiimod:     1.0.0+cvc.1 → 1.0.0+cvc.2
  grpc:         1.68.2+cvc.1 → 1.68.2+cvc.2
  vtk:          9.4.2+cvc.1 → 9.4.2+cvc.2
```

Tests:
- Full cascade from a leaf.
- `--dry-run` vs actual modification.
- `--platform` filtering narrows the cascade set.

#### 2.3 — `cascade-rebuild`

Combines `cascade-bump` + `build_all()` + `publish` for the
affected set.  This is the "one command to rule them all" for
patching a published component.

Tests:
- Integration test with mock server: bump → build → publish loop.
- `--dry-run` shows plan without building.
- `--keep-going` continues past individual build failures.

### Phase 3: Server / Catalog Enhancements

| # | Task | Files | Tests |
|---|---|---|---|
| 3.1 | Add `supersedes`, `rebuild_reason`, `rebuild_trigger` fields | `db.py`, `manifest.py` | `test_server.py` |
| 3.2 | Chain-hash duplicate warning at publish | `app.py` | `test_server.py` |
| 3.3 | DB migration for new columns | `migrations/` | — |

#### 3.1 — Rebuild provenance fields

Add three optional columns to `PackageRow`:

```python
supersedes = Column(Text, default="")       # "1.3.1+cvc.1"
rebuild_reason = Column(Text, default="")   # "fix macOS arm64"
rebuild_trigger = Column(Text, default="")  # "zlib"
```

Expose in `CatalogEntry`, manifest schema v4, and the publish API.

#### 3.2 — Chain-hash duplicate warning

When publishing version `X+cvc.N` and version `X+cvc.(N-1)` exists
with the **same** `recipe_sha256` (chain hash), return a warning
(but still allow the publish):

```
warning: zlib 1.3.1+cvc.2 has identical chain_hash to 1.3.1+cvc.1;
the rebuild may be unnecessary.
```

This catches accidental revision bumps where nothing actually changed.

---

## Test Matrix

### Unit Tests (Phase 1)

| Test | Inputs | Expected |
|---|---|---|
| `test_chain_hash_single_recipe` | Recipe with no deps | Deterministic 64-char hex |
| `test_chain_hash_changes_on_recipe_edit` | Edit recipe.yaml | Hash changes |
| `test_chain_hash_changes_on_dep_edit` | Edit dependency recipe | Hash changes |
| `test_chain_hash_diamond` | A→B→D, A→C→D; edit D | All hashes change |
| `test_chain_hash_unrelated_change` | Edit unrelated recipe | Hash unchanged |
| `test_chain_hash_cycle` | A→B→A | Returns "" for cycle |
| `test_chain_hash_platform_filter` | Platform-conditional dep | Different hashes per platform |
| `test_cascade_set_leaf` | Leaf component | Returns `[leaf]` |
| `test_cascade_set_direct` | One dependent | Returns `[target, dep]` |
| `test_cascade_set_transitive` | A→B→C | Returns `[C, B, A]` |
| `test_cascade_set_diamond` | Diamond graph | Correct topo order |
| `test_cascade_set_unknown` | Unknown component | `KeyError` |
| `test_revision_bump_basic` | `cvc.1` recipe | File updated, returns `(old, new)` |
| `test_revision_bump_missing_field` | No `cvc_revision` | Defaults 0→1 |
| `test_revision_bump_preserves_yaml` | Complex recipe | Other fields unchanged |
| `test_cascade_bump_full` | Leaf with 3 dependents | All bumped in order |
| `test_cascade_bump_dry_run` | `dry_run=True` | No files modified |

### CLI Tests (Phase 2)

| Test | Command | Expected |
|---|---|---|
| `test_revision_bump_cli` | `revision-bump zlib` | Recipe file updated |
| `test_revision_bump_dry_run` | `revision-bump zlib --dry-run` | No file change |
| `test_cascade_bump_cli` | `cascade-bump zlib` | All dependents bumped |
| `test_cascade_bump_dry_run` | `cascade-bump zlib --dry-run` | Summary printed |
| `test_cascade_rebuild_dry_run` | `cascade-rebuild zlib --dry-run` | Plan printed |

### Integration Tests (Phase 2–3)

| Test | Scenario | Expected |
|---|---|---|
| `test_cascade_rebuild_e2e` | Bump zlib, build cascade, publish to mock server | All archives published with correct versions |
| `test_publish_supersedes` | Publish `+cvc.2` after `+cvc.1` | `supersedes` field set |
| `test_chain_hash_dup_warning` | Publish with identical chain hash | Warning emitted, publish succeeds |
| `test_resolver_picks_latest_revision` | Catalog has `+cvc.1` and `+cvc.2` | Resolver picks `+cvc.2` |

---

## Open Questions

1. **Should cascade-bump auto-commit?**  Leaning no — let the
   developer review the changes and commit manually.  The
   `cascade-rebuild` command could optionally `git add` the
   modified recipe files.

2. **Should we support partial cascades?**  e.g. "bump zlib and
   only rebuild for linux/x86_64/release/shared."  This is needed
   for CI sharding but complicates the version numbering — the
   revision is version-level, not variant-level.  Recommendation:
   bump the revision once, then build all needed variants.

3. **Lock on recipe.yaml format?**  `revision_bump()` needs to
   modify `recipe.yaml` in-place.  We should use `ruamel.yaml`
   (round-trip preserving) instead of PyYAML to avoid reformatting
   the file.  Check if it's already a dependency.

4. **Catalog revision semantics.**  The server catalog has a
   monotonic `revision` counter (incremented on each save).
   Should the cascade-rebuild atomically bump the catalog revision
   once for the entire cascade, or once per published package?
   Recommendation: once per package (simpler, matches current
   semantics).

5. **SemVer build metadata comparison.**  Per SemVer spec, build
   metadata is **ignored** in version comparisons (`1.3.1+cvc.1 ==
   1.3.1+cvc.2` in `__eq__`).  The resolver currently compares
   `Version` objects.  We need to ensure the resolver prefers
   the **highest cvc_revision** when multiple revisions of the same
   upstream version are in the catalog.  Currently, candidates are
   sorted descending — but since build metadata is ignored in `<`,
   the sort order between `+cvc.1` and `+cvc.2` is arbitrary.
   **This is a bug that must be fixed.**

---

## Appendix: Revision Semantics & SemVer

The `+cvc.N` suffix uses SemVer **build metadata**, which is
explicitly excluded from version precedence (§10 of the spec).
This means:

- `satisfies("1.3.1+cvc.2", "==1.3.1")` → **True** (metadata ignored)
- `Version("1.3.1+cvc.1") == Version("1.3.1+cvc.2")` → **True**
- Sort order between revisions is **undefined** by SemVer

For the resolver to correctly prefer the latest revision, we need
a secondary sort on `cvc_revision` when two candidates have equal
SemVer precedence.  This requires a small change to the candidate
sorting in `resolve()`:

```python
# Current: sorted by Version descending
candidates[name].sort(key=lambda e: Version.parse(e.version), reverse=True)

# Fixed: sort by (Version desc, cvc_revision desc)
candidates[name].sort(
    key=lambda e: (Version.parse(e.version), -e.cvc_revision),
    reverse=True,
)
```

Actually, since `reverse=True` is used, the `cvc_revision` should
be positive (not negated):

```python
candidates[name].sort(
    key=lambda e: (Version.parse(e.version), e.cvc_revision),
    reverse=True,
)
```

This ensures `+cvc.2` is tried before `+cvc.1` when both satisfy
the version constraint.
