# Authoring recipes

A **recipe** tells cvcpkg how to build one component from upstream source.
Recipes live in `recipes/<name>/` and consist of a `recipe.yaml` plus one
or more build scripts. This guide walks through creating one.

## Recipe ownership — where a recipe lives

**A project owns the cvcpkg recipe for its own package, in its own repository.**
This repo's `recipes/` set is the **shared dependency ecosystem** — the
third-party libraries and toolchains that many CVC projects consume (Boost,
Qt6, VTK, CGAL, HDF5, the CUDA-math libs, the Python interpreters, …).

- **First-party CVC packages carry their own recipe.** `libcvc`, `volrover`,
  and `grl-snam` each keep their cvcpkg recipe under a `cvcpkg/recipes/<name>/`
  directory in *their* repo, next to a publish workflow, and publish under the
  `cvc` organization. Do **not** add them here.
- **This repo holds the dependency stack** that those packages build against.
- Consume a project-owned recipe with the `--recipes-dir` overlay
  (`cvcpkg <cmd> --recipes-dir cvcpkg/recipes …`); cvcpkg merges overlays over
  the shared set, later directories winning.

Rationale: a package's build definition belongs with the package it builds, so
it versions and releases together and its maintainers own it — the dependency
ecosystem here stays a clean, shared foundation rather than a dumping ground
for every downstream project.

## Scaffold with `cvcpkg init`

The fastest start is the scaffolder:

```bash
cvcpkg init mylib --build-system cmake \
    --version 1.2.3 \
    --url https://example.org/mylib-1.2.3.tar.gz
```

This creates `recipes/mylib/` with a schema-valid `recipe.yaml`, a
`build.sh` (and, for CMake, a `build.ps1`) that source the shared build
helpers. Supported build systems: `cmake`, `meson`, `autotools`.

Then fill in the `TODO` placeholders (source SHA-256, dependencies, package
globs) and validate:

```bash
python packaging/validate.py recipes/mylib
```

## Generate from an existing project with `cvcpkg generate`

Where `init` leaves every field a `TODO`, `cvcpkg generate` starts from a
project that already builds:

```bash
cvcpkg generate ../mylib          # detect and write recipes/mylib/
cvcpkg generate . --dry-run       # print what it would write
```

It detects the build system — CMake, Meson, Autotools, a plain Makefile, or
Python packaging (PEP 621 `pyproject.toml`, Poetry, `setup.cfg`) — reads the
metadata the project already declares (name, version, description, homepage,
license, dependencies) and writes a `recipe.yaml` with those fields filled
in, plus a matching `build.sh` (and a `build.ps1` for CMake and Python
projects, the two with a Windows story out of the box). A git checkout also
gets its `source:` block prefilled from the `origin` remote and current
commit. If a project carries both a `CMakeLists.txt` and a `pyproject.toml`
(scikit-build, pybind11), Python wins: that is the entry point that installs
the importable package.

Two rules shape the output:

- **It never emits a dependency that does not resolve.** Detected dependency
  names (CMake packages, pkg-config modules, PyPI names) are matched against
  the recipe set cvcpkg can actually resolve — including `provides` slots —
  and anything unmatched becomes a commented suggestion, so the result
  passes `cvcpkg validate` as generated.
- **Anything the project did not state is written as a `TODO`**, so guesses
  surface in review instead of silently shipping a wrong value.

Everything that resolves is filed under `depends.build`, with `runtime` left
empty — review that split before publishing: a library the project links
against belongs in `depends.runtime` or it will not ship (see
[key fields](#key-fields)). Note also that a generated Python recipe builds
the project tree with `pip install . --no-deps`; a package headed for the
shared per-interpreter `-cpXXX` matrix uses the `python_wheel` /
`python_sdist` model from [python-wheels.md](python-wheels.md) instead.

| Flag | Meaning |
|------|---------|
| `--build-system {auto,cmake,autotools,meson,make,python}` | Override detection (default `auto`). |
| `--name` | Recipe name (default: the project's own). |
| `--dir` | Recipes directory to create the recipe in (default `recipes`). |
| `--recipes-dir` | Recipe directory to resolve detected dependencies against (repeatable). |
| `--no-default-recipes` | Do not consult the default recipe set when resolving dependencies. |
| `--dry-run` | Print the recipe instead of writing it. |
| `--force` | Overwrite an existing recipe directory. |

If no build system can be detected, the command fails and names the marker
files it looked for — pass `--build-system` to force one, or fall back to
`cvcpkg init` for a blank scaffold.

## `recipe.yaml` structure

```yaml
schema_version: 1

recipe:
  name: mylib               # lowercase, digits, hyphens; starts with a letter
  upstream_version: "1.2.3"
  cvc_revision: 1           # bump when you change the build without a new upstream version
  maintainer: "Your Name"
  license: MIT              # SPDX expression
  homepage: https://example.org/
  description: >-
    One-line description of the component.
  tags: [math]

source:
  type: tarball             # tarball | git | vendored | prebuilt | none | ...
  url: https://example.org/mylib-1.2.3.tar.gz
  sha256: "…64 hex chars…"  # verified on download
  strip_components: 1

patches: []                 # patch files applied with patch -p1, in order

depends:
  build: []                 # needed only to build
  runtime:                  # needed at runtime (recorded in the manifest)
    - name: zlib
      version: "^1.3"
  host_tools:               # tools that must be on PATH during the build
    - cmake
    - ninja

build:
  matrix:
    - platform: linux
      script: build.sh
    - platform: macos
      script: build.sh
    - platform: windows
      script: build.ps1

package:
  files:                    # DECLARATIVE: what this package is expected to
    - include/              # contain. NOT a filter — see below.
    - lib/libmylib.*
  cmake_packages:           # optional: CMake targets this component provides
    - { name: mylib, targets: ["mylib::mylib"] }
  pkg_config:               # optional: pkg-config modules shipped
    - mylib
```

> **`package.files` does not filter anything.** `cvcpkg pack` copies the
> *entire* `CVC_INSTALL_DIR` into the bundle and derives the manifest's
> `contents.files` from the real staged tree, so what your build script
> installs is exactly what ships — a path you did not declare still goes out,
> and nothing warns you. What scopes a package is that each recipe installs
> into its own empty `CVC_INSTALL_DIR`: if `make install` drops more than you
> want to publish, prune it in the build script. A recipe declaring `bin/` once
> shipped FFmpeg's entire library, header and pkg-config set into a consumer
> prefix this way, where it sat for months — the declaration was right, and
> nothing enforced it.

The authoritative schema is
[`src/cvcpkg/schemas/recipe-schema.yaml`](../src/cvcpkg/schemas/recipe-schema.yaml)
(it ships inside the `cvcpkg` package); `cvcpkg validate` — and its shim
`packaging/validate.py` — enforce it in CI. A downstream project can validate
its own recipes with `cvcpkg validate ./cvcpkg/recipes/<name>` or
`cvcpkg validate --recipes-dir cvcpkg/recipes`, no libcvc-deps checkout required.

### Key fields

- **`cvc_revision`** — the cvcpkg-specific build revision. The published
  version is `"<upstream_version>+cvc.<cvc_revision>"`. Bump it when you
  change the build for the same upstream version; `cvcpkg upgrade` treats a
  higher `+cvc.N` as newer. You rarely need to edit this by hand for a
  republish: `cvcpkg pack --bump` stamps the next free revision above what is
  already published at pack time (leaving the recipe untouched), and
  `cvcpkg cascade-bump <name>` rewrites it for a package and its dependents when
  you *do* want the bump committed. See
  [revision-bump-cascade.md](roadmap/revision-bump-cascade.md).
- **`depends.runtime` vs `depends.build`** — this split decides **placement**,
  so file deps carefully. Runtime deps are recorded in the manifest and
  installed into the deliverable install prefix (they ship); build deps go to
  the build prefix and are stripped on install unless `--keep-build-prefix`.
  A build-only tool filed under `runtime` will wrongly ship; a linked library
  filed under `build` will wrongly not ship. `platforms: [linux, macos]` on a
  dep scopes it.
- **`package.files`** — glob patterns selecting what ends up in the bundle.
  Use `lib/*/…` variants to catch Debian multiarch paths (e.g.
  `lib/x86_64-linux-gnu/`).
- **Python packages** are a special case: they use the `python_wheel` /
  `python_sdist` source types plus a `python:` block naming the target
  interpreter and ABI, and every Python package is published as a matrix of
  per-interpreter column recipes (`-cp311`, `-cp312`, `-cp313`, `-cp313t`).
  See [python-wheels.md](python-wheels.md) before authoring one.

### Cross-compilation host tools

Two kinds of build-time tooling exist, and they are treated differently:

- **`depends.host_tools`** — tools that must be on `PATH` during *this*
  recipe's build (e.g. `cmake`, `ninja`). They are resolved and made
  available for the build but are not part of the recipe's own output.
- **`cross_toolchain`** — declared by a recipe that *is itself* a cross
  toolchain (e.g. `emsdk`, a bazel-based toolchain). It lists the target
  platforms it enables and any env vars to export into dependent builds:

  ```yaml
  cross_toolchain:
    target_platforms: [wasm]        # targets this toolchain enables
    env:
      CVC_BAZEL_BIN: "${PREFIX}/bin/bazel"   # ${PREFIX} = the build prefix
  ```

A recipe with a `cross_toolchain` block is a **host tool**: its bundle
manifest carries `bundle.host_tool: true`, and when it is auto-pulled to
cross-build another package it installs into the **separate build prefix**,
never into the deliverable `--prefix`.  More generally, **placement follows the
dependency edge** — `depends.build`/`depends.host_tools` land in the build
prefix (stripped on install unless `--keep-build-prefix`), `depends.runtime`
lands in the install prefix and ships.  File deps accordingly: a build-only tool
under `runtime` will now wrongly ship, and a linked library under `build` will
wrongly not ship.  See
[Build-time deps stay out of the deliverable](cvcpkg-builder-wsl-windows-cross.md#build-time-deps-stay-out-of-the-deliverable)
in the cross-build guide for the `--build-prefix` / `--keep-build-prefix`
CLI semantics and the `share/libcvc-deps/host-tools.yaml` record, and
[source recipes](source-recipes.md) for source packages.

## Build scripts

Build scripts run with these environment variables set:

| Variable | Meaning |
|----------|---------|
| `CVC_SOURCE_DIR` | Extracted upstream source tree. |
| `CVC_BUILD_DIR` | Out-of-tree build directory. |
| `CVC_INSTALL_DIR` | Where to install (the staged prefix). |
| `CVC_DEPS_PREFIX` | Install prefix — this recipe's **runtime** deps (headers/libs you link). |
| `CVC_BUILD_PREFIX` | Build prefix — the **build** closure: host tools on `PATH`, staged source packages at `src/<name>`. |
| `CVC_JOBS` | Parallelism (`-j`). |
| `CVC_PLATFORM` | `linux`, `macos`, `windows`, a BSD, `wasm`, … |

A minimal CMake `build.sh` is just:

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build -DMYLIB_BUILD_TESTS=OFF     # extra -D flags as needed
```

`cvc_cmake_build` (bash) and `Invoke-CvcCMakeBuild` (PowerShell) configure,
build, and install with the right prefix and dependency paths. Meson and
Autotools scaffolds call `meson`/`./configure` directly against the
`CVC_*` variables — see the scaffolded `build.sh` for the exact commands.

## Test, validate, publish

```bash
# Validate the recipe against the schema + check referenced scripts exist
python packaging/validate.py recipes/mylib

# Build it locally
cvcpkg build mylib --recipes-dir recipes --prefix /tmp/mylib-prefix

# Publish the recipe to a server (maintainers)
cvcpkg recipe push mylib --server "$CVCPKG_SERVER_URL" --token "$CVCPKG_TOKEN"
```

Once a recipe is on the server, the builder fleet can build it for every
platform and publish the resulting bundles to the catalog.

Pushed recipes are also **browsable**. The server's package page
(`/package/<name>`) shows the recipe: the yaml, its build scripts and
patches. The same content is served over the API — `GET /v1/recipe/{name}`
returns the raw `recipe.yaml`, with `…/files` (list every artifact),
`…/file?path=…` (fetch one) and `…/archive` (tar.gz or zip) companions;
scope organization recipes with `?org=<org>`. The archive extracts into a
well-formed recipe directory — `<name>/` plus the shared `_common/` helpers
its scripts source — so it is directly usable with
`cvcpkg build <name> --recipes-dir <dir>`. See
[api-reference.md](api-reference.md).
