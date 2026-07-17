# Source Recipes (File-Artifact Packages)

Some cvcpkg packages are **just source files** — a header-only tree, a
vendored source drop, a patch set, a data blob — with no compilation.
cvcpkg publishes and consumes these directly, reusing the existing recipe
infrastructure rather than introducing a separate recipe *type*.

## What makes a recipe a "source recipe"

A source recipe is an ordinary recipe that **announces it is a file
artifact**:

- **`platform: any`** — source is valid everywhere, so it is built **once**
  and served to every platform.  Its architecture is `noarch` automatically.
- **No toolchain / no compilation** — the build script only *stages* the
  (patched) source tree; the output archive *is* the source.
- The only processing allowed is **patches** (applied before staging) and
  optional packaging scripts.

There is **no `source` recipe type** — it is a normal recipe whose build
matrix is entirely `platform: any` and whose build step stages files.

## Canonical layout

A source recipe stages its tree under a stable, discoverable path — its own
install dir, which *is* the package payload:

```
$CVC_INSTALL_DIR/src/<recipe-name>/
```

Because it is consumed as a build dependency, that payload is installed into the
**build prefix**, so consumers read it at `$CVC_BUILD_PREFIX/src/<recipe-name>/`.

The `_common/stage-source.sh` (and `stage-source.ps1`) helper does this:

```bash
#!/usr/bin/env bash
set -euo pipefail
. "$(dirname "$0")/../_common/stage-source.sh"
cvc_stage_source                 # stage the whole (patched) source tree
# or: cvc_stage_source include src   # only these subpaths
```

```mermaid
flowchart LR
    UP["upstream source<br/>(vendored / tarball)"] --> P["apply patches"]
    P --> STAGE["cvc_stage_source<br/>→ $CVC_INSTALL_DIR/src/&lt;name&gt;/"]
    STAGE --> PKG[("source package<br/>platform: any · noarch")]
    PKG -->|depends.build| DOWN["downstream recipe<br/>(linux / windows / …)"]
    DOWN --> BIN[("platform/arch binary")]
```

## Placement: the dependency edge decides

cvcpkg places a package by **how it is depended upon**, not by what it is:

| edge | goes to | ships? |
|------|---------|--------|
| `depends.runtime` | the **install prefix** (`--prefix`) | yes |
| `depends.build` / `depends.host_tools` | the **build prefix** (`--build-prefix`, default `<prefix>.build`) | no — stripped on install |

A source recipe is consumed as a **build** dependency, so it stages into the
build prefix at `$CVC_BUILD_PREFIX/src/<name>/` and never pollutes the
deliverable.  Nothing about being "source" or `platform: any` routes it — being
a build dep does.  The same `any` package declared under `depends.runtime` is a
first-class deliverable and **does** ship (e.g. a noarch data or config
package).

Precisely, the install prefix receives the target's *runtime closure* (runtime
deps, transitively); the build prefix receives everything reachable only via a
build edge, plus those deps' own runtime deps so they can actually run.  A
package reachable both ways ships (and stays visible to the build).

If you *want* the sources in the distribution, keep the build prefix:

```bash
cvcpkg build myapp --prefix ./out --keep-build-prefix   # sources stay in ./out.build
cvcpkg install ... --keep-build-prefix                  # install won't strip it
```

Build scripts get two search roots: **`CVC_DEPS_PREFIX`** (install prefix —
runtime deps you link) and **`CVC_BUILD_PREFIX`** (build prefix — host tools on
`PATH`, staged sources at `src/<name>`).

## Downstream consumption

A platform/arch recipe consumes a source recipe by declaring a **build
dependency** on it.  The builder builds the source recipe first into the build
prefix, so the consumer finds the staged tree at
`$CVC_BUILD_PREFIX/src/<name>/`:

```yaml
# downstream recipe.yaml
depends:
  build:
    - mathsrc          # the source recipe
build:
  matrix:
    - platform: linux
      script: build.sh
```

```bash
# downstream build.sh
. "$(dirname "$0")/../_common/stage-source.sh"
SRC="$(cvc_source_dir_of mathsrc)"      # $CVC_BUILD_PREFIX/src/mathsrc
cc -I "$SRC" -o "$CVC_INSTALL_DIR/bin/app" "$CVC_SOURCE_DIR/main.c" "$SRC/impl.c"
```

`cvc_source_dir_of` resolves against `CVC_BUILD_PREFIX`, falling back to
`CVC_DEPS_PREFIX` when the build prefix is not separated.

This gives a clean split: **one authoritative, checksummed source package**
feeds **many binary variants** — reproducible, mirror-friendly, and built
without re-fetching upstream for every platform.

## Why this shape

- **Reuses existing infrastructure** — the `platform: any` build class
  (already handled in the builder's build-order and matrix logic), vendored
  /`prebuilt` source staging, and the normal `depends` graph.  No new schema
  concepts.
- **Built once** — an `any/noarch` package is not fanned out per platform.
- **Deduplicated source of truth** — downstream recipes never each vendor
  their own copy of a shared source tree.

## Canonization

The end-to-end contract is locked in by
[`tests/integration/test_source_recipe_workflow.py`](../tests/integration/test_source_recipe_workflow.py):
it builds a source recipe (`any`), then a downstream recipe that consumes the
staged source and **compiles a real binary**, and asserts the binary runs —
so the source→binary workflow cannot silently regress.  A second case
(`test_source_stages_into_build_prefix_not_the_deliverable`) locks the placement
contract: with a build prefix, the staged source lands there, the deliverable
prefix stays free of `src/`, and the consumer's binary still ships and runs.
