# Authoring recipes

A **recipe** tells cvcpkg how to build one component from upstream source.
Recipes live in `recipes/<name>/` and consist of a `recipe.yaml` plus one
or more build scripts. This guide walks through creating one.

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
  type: tarball             # tarball | git | vendored | prebuilt | ...
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
  files:                    # globs (relative to the install prefix) to ship
    - include/
    - lib/libmylib.*
  cmake_packages:           # optional: CMake targets this component provides
    - { name: mylib, targets: ["mylib::mylib"] }
  pkg_config:               # optional: pkg-config modules shipped
    - mylib
```

The authoritative schema is
[`packaging/schemas/recipe-schema.yaml`](../packaging/schemas/recipe-schema.yaml);
`packaging/validate.py` enforces it in CI.

### Key fields

- **`cvc_revision`** — the cvcpkg-specific build revision. The published
  version is `"<upstream_version>+cvc.<cvc_revision>"`. Bump it when you
  change the build for the same upstream version; `cvcpkg upgrade` treats a
  higher `+cvc.N` as newer.
- **`depends.runtime` vs `depends.build`** — runtime deps are recorded in
  the package manifest and installed alongside the component; build-only
  deps are not. `platforms: [linux, macos]` on a dep scopes it.
- **`package.files`** — glob patterns selecting what ends up in the bundle.
  Use `lib/*/…` variants to catch Debian multiarch paths (e.g.
  `lib/x86_64-linux-gnu/`).

## Build scripts

Build scripts run with these environment variables set:

| Variable | Meaning |
|----------|---------|
| `CVC_SOURCE_DIR` | Extracted upstream source tree. |
| `CVC_BUILD_DIR` | Out-of-tree build directory. |
| `CVC_INSTALL_DIR` | Where to install (the staged prefix). |
| `CVC_DEPS_PREFIX` | Prefix containing this recipe's dependencies. |
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
