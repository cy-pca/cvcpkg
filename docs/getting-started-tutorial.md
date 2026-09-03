# Getting started: publishing your first package

A practical walkthrough of the recipe-first workflow: scaffold a recipe,
validate it, build it against prebuilt dependencies, pack it, and publish it
to a cvcpkg server so anyone can install it with `cvcpkg install`.

---

## Prerequisites

- A **cvcpkg server** (the public server at `https://cvcpkg.org`, or a
  self-hosted instance) — only needed for the publish/remote-build steps.
- An **API token** with the `publisher` or `admin` role for publishing
  (step 2 shows how to get one).
- For local builds: **CMake, Ninja, and a C/C++ compiler**. `cvcpkg doctor`
  checks all of these.

## Step 1: install the cvcpkg CLI

The quick installer downloads the latest standalone binary for your
platform, verifies its SHA-256, and installs it to `~/.local/bin`:

```bash
curl -fsSL https://cvcpkg.org/install.sh | sh
```

On Windows (installs to `%LOCALAPPDATA%\cvcpkg` instead):

```powershell
irm https://cvcpkg.org/install.ps1 | iex
```

Set `CVCPKG_INSTALL_DIR` to change the install location, or `CVCPKG_VERSION`
to pin a release tag. If there is no prebuilt binary for your platform,
install from PyPI instead (Python ≥ 3.10 required):

```bash
pip install cvcpkg
```

See [pypi-install.md](pypi-install.md) for the pip extras (server, database
backends). Either way, verify the install and your local toolchain:

```bash
cvcpkg --version
cvcpkg doctor          # checks Python, CMake, Ninja, compiler, git
```

## Step 2: authenticate

Publishing needs a bearer token. If the server allows self-registration:

```bash
cvcpkg register --server https://cvcpkg.org \
  --name your-name --email you@example.com --role publisher
```

Depending on the server's registration mode you either get a token
immediately (open mode) or your request is queued for admin approval.
Alternatively, a server admin can mint one for you with
`cvcpkg token create --name your-name --role publisher`.

Export the token and (optionally) the server URL — most commands read both
from the environment:

```bash
export CVCPKG_SERVER_URL="https://cvcpkg.org"   # this is the default
export CVCPKG_TOKEN="cvctok_..."
```

To keep secrets out of shell history and `ps` output, put `KEY=VALUE` lines
in an env file instead: cvcpkg automatically reads `./.cvcpkg.env`,
`~/.config/cvcpkg/env`, and `/etc/cvcpkg/env` when present, or an explicit
path via `cvcpkg --env-file PATH ...`.

Test the connection:

```bash
cvcpkg search zlib
```

## Step 3: scaffold a recipe

A recipe is a directory — `recipes/<name>/` — holding a `recipe.yaml` plus
one build script per platform family. From your project's repo root:

```bash
cvcpkg init mylib --version 1.2.3 \
  --url https://example.org/mylib-1.2.3.tar.gz
```

This creates `recipes/mylib/recipe.yaml`, `build.sh` (Linux/macOS/BSD), and
`build.ps1` (Windows, for the default `--build-system cmake`; `meson` and
`autotools` are also supported). For an existing buildable project,
`cvcpkg generate <project-dir>` goes further: it detects the build system,
reads the metadata the project already declares, and fills the recipe in.

Two things make this "recipe-first" flow convenient:

- **CWD auto-overlay** — a `./recipes` directory in your current working
  directory is automatically overlaid on the recipe search path (and wins on
  name conflicts), so `cvcpkg build mylib` from the repo root just works, no
  `--recipes-dir` needed.
- **Shared `_common/` helpers** — the scaffolded build scripts source
  `../_common/env-<platform>.sh` for shared environment setup and helpers
  like `cvc_cmake_build`. Copy them into your recipes tree once:

```bash
cvcpkg recipe sync-common ./recipes
```

Re-run that after upgrading cvcpkg to pick up new helpers.

## Step 4: fill in the recipe

A complete `recipe.yaml` validated against the recipe schema (see
`recipes/zlib/recipe.yaml` in this repo for a real production example).
The `sha256` below is a placeholder — `cvcpkg validate` rejects it until you
replace it with a real 64-hex-character digest, computed in the next step:

```yaml
schema_version: 1
recipe:
  name: mylib
  upstream_version: "1.2.3"
  cvc_revision: 1
  description: "My library for downstream consumers."
  homepage: https://example.org
  license: MIT
  tags: [utils]

source:
  type: tarball
  url: https://example.org/mylib-1.2.3.tar.gz
  sha256: "<64-hex-char sha256 of the tarball>"
  strip_components: 1

patches: []

depends:
  build:
    - zlib          # library deps, installed before the build
  runtime: []       # deps needed at runtime (included in manifests)
  host_tools:
    - cmake         # build tools; consumers' CI usually provides these
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
  files:
    - include/
    - lib/libmylib*
    - lib/cmake/mylib/
  cmake_packages:
    - { name: mylib, targets: ["mylib::mylib"] }
```

### Key fields

| Field | Description |
|---|---|
| `recipe.name` | Package name: lowercase letters, digits, hyphens; must start with a letter. |
| `recipe.upstream_version` | The upstream library version. |
| `recipe.cvc_revision` | Integer ≥ 1. The minted catalog version is `{upstream_version}+cvc.{cvc_revision}`; bump the revision when the recipe changes without an upstream bump (see step 8). |
| `source` | Where the source comes from. `type: tarball` needs `url` + `sha256`; `type: git` needs `url` + a full 40-char `commit` (tags are mutable and rejected). |
| `depends.build` / `depends.runtime` | Other cvcpkg packages, installed into the prefix before the build / recorded as runtime deps. |
| `depends.host_tools` | Build tools (cmake, ninja, …). Excluded from `install-deps` by default. |
| `build.matrix` | One entry per target platform; each names the build `script` to run and may set extra `env`. `platform: any` marks a platform-independent package built once for all platforms. |
| `package.files` | Glob patterns, relative to the install prefix, selecting the files that ship in the archive. |
| `package.cmake_packages` | CMake package/target names so downstream projects can `find_package()` this library. |

Compute the source hash:

```bash
curl -sL https://example.org/mylib-1.2.3.tar.gz | sha256sum
```

### The build script

The scaffolded `build.sh` is usually all you need for a CMake project:

```bash
#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Configure, build, and install with CMake.  Pass extra -D flags as needed.
cvc_cmake_build
```

`cvc_cmake_build` runs configure + build + install with the right
`CMAKE_INSTALL_PREFIX`, build type, and shared/static settings. The builder
exports `CVC_SOURCE_DIR`, `CVC_BUILD_DIR`, `CVC_INSTALL_DIR`,
`CVC_DEPS_PREFIX` (where dependencies are installed), `CVC_PLATFORM`,
`CVC_CONFIG`, and `CVC_LINK` for scripts that need to do their own thing.
See [recipe-authoring.md](recipe-authoring.md) for the full reference.

## Step 5: validate

```bash
cvcpkg validate recipes/mylib
```

This checks schema conformance, that referenced build scripts and patches
exist, that the minted version is orderable SemVer, and that every
cross-recipe dependency resolves. Note that `validate` does not *execute*
build scripts — a recipe can validate green and still fail to build, so the
next two steps are the real test.

## Step 6: install prebuilt dependencies

Instead of compiling your dependency closure from source, pull prebuilt
bundles from the catalog into a local prefix:

```bash
cvcpkg install-deps mylib --prefix ./deps
```

This resolves the recipe's `depends.build` + `depends.runtime` transitively
and installs them into `./deps`. Host tools (cmake/ninja/…) are excluded by
default — pass `--include-host-tools` if you want those from the catalog too.

## Step 7: build locally

```bash
cvcpkg build mylib --prefix ./deps
```

By default `build` compiles **only the named recipe** (`--no-deps`) and
assumes its dependencies are already in `--prefix` — which step 6 just
arranged. Useful variations:

```bash
cvcpkg build mylib --prefix ./deps --incremental   # reuse the build tree; fast re-runs
cvcpkg build mylib --prefix ./deps --with-deps     # build the whole closure from source
cvcpkg build mylib --prefix ./deps --config debug --link static
cvcpkg build mylib --prefix ./deps --local         # bundled/local recipes only, no server
```

`--incremental` keys a stable build tree on (recipe, platform, config,
link), so a re-run recompiles only what changed — the dev-iteration mode.
Without `--local`, cvcpkg also fetches the latest recipe set from the
server; your `./recipes` overlay still wins for `mylib` itself.

## Step 8: pack

Packing builds the recipe and archives the installed files together with a
`manifest.yaml` and SHA-256 checksum into a distributable `.tar.gz`:

```bash
cvcpkg pack mylib --prefix ./deps --output-dir ./dist --bump
```

`--bump` matters: published variants are **immutable**, so republishing the
same `name + version + cvc_revision` is rejected. `--bump` queries the
server for the highest published `+cvc.N` and packs one above it (never
below the recipe's committed revision), so a republish never collides — no
recipe editing required. Add `--bump-write` to also write the resolved
revision back into `recipe.yaml` so you can commit it. Related commands:
`cvcpkg next-revision` (compute without packing), `cvcpkg rev-bump` (offline
`+1` edit of the recipe and, by default, its dependents), `cvcpkg
cascade-bump` (server-aware: bumps a recipe and its dependents to one above
what is already published).

If you already have a staged install tree built by your own toolchain, skip
the build with `--from-prefix`:

```bash
cvcpkg pack recipes/mylib --from-prefix ./stage --output-dir ./dist --bump
```

## Step 9: publish

```bash
cvcpkg publish mylib
```

With `CVCPKG_SERVER_URL` and `CVCPKG_TOKEN` exported that is all; otherwise
pass `--server` and `--token` explicitly. `publish` looks in `--output-dir`
(default `./dist`) for the archive matching the current
`--platform/--config/--link` tuple. Use `--all` to publish every archive in
the output directory that matches that same platform/config/link tuple, and
`--org my-team` to publish under an organization namespace (see
[organizations.md](organizations.md)).

A `409` response means that exact variant is already published — the CLI
reports it as `skipped (already published)`. Repack with `--bump`.

## Step 10: remote builds

For multi-platform coverage, let the builder fleet do the work. Remote
builders fetch recipes from the server, so push yours first:

```bash
cvcpkg recipe push mylib
```

This bundles the recipe directory (plus the shared `_common/` helpers) and
uploads it. Then submit build jobs — each builder claims a job, installs the
recipe's dependencies, builds, and **publishes the resulting archive
itself**:

```bash
# One job:
cvcpkg builds submit --recipe mylib --platform linux --arch x86_64 --wait

# A DAG across platforms — dependencies are resolved and ordered
# automatically, and unpublished deps are built first:
cvcpkg builds submit-dag mylib --platform linux,macos --arch x86_64,arm64
```

`submit-dag` prints a DAG id. Follow the interleaved live logs of every job
in it, or watch the whole fleet:

```bash
cvcpkg builds follow-dag <dag_id>
cvcpkg builds monitor          # live 'top' for the fleet
```

See [cvcpkg-remote-builders.md](cvcpkg-remote-builders.md) for running your
own builder agents.

## Step 11: verify the published package

Once published, anyone can find and install it:

```bash
cvcpkg search mylib
cvcpkg info mylib
cvcpkg install mylib --prefix ./consume
```

Pin an exact version, or install an org-scoped package with an
org-qualified name:

```bash
cvcpkg install "mylib==1.2.3+cvc.1" --prefix ./consume
cvcpkg install my-team/mylib --prefix ./consume
```

Private org packages require `CVCPKG_TOKEN` to be set. `cvcpkg install`
writes a lockfile into the prefix; `cvcpkg verify --prefix ./consume`
checks the prefix against it later.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `401 Unauthorized` | Token missing or expired | Set `CVCPKG_TOKEN` (or use an env file) |
| `403 Forbidden` | Token lacks the `publisher` role | Ask an admin, or `cvcpkg token create --role publisher` |
| `skipped (already published)` / `409` | Variant already exists at this `cvc_revision` — published variants are immutable | Repack with `pack --bump` and publish again |
| Recipe validates but the build fails | `validate` checks the YAML and that scripts exist; it does not run them | Actually run `cvcpkg build` locally before publishing |
| `build.sh: .../_common/env-linux.sh: No such file` | Your recipes tree has no `_common/` helpers | `cvcpkg recipe sync-common ./recipes` |
| Recipe change had no effect on installs | Republished without bumping `cvc_revision` | Bump (or `pack --bump`) — same-version publishes are rejected, not replaced |

## Summary

1. **Install** the CLI: `curl -fsSL https://cvcpkg.org/install.sh | sh`
2. **Authenticate**: export `CVCPKG_SERVER_URL` / `CVCPKG_TOKEN`
3. **Scaffold**: `cvcpkg init mylib` (or `cvcpkg generate`), then `cvcpkg recipe sync-common ./recipes`
4. **Fill in** `recipe.yaml`: source URL + SHA-256, deps, package globs
5. **Validate**: `cvcpkg validate recipes/mylib`
6. **Install deps**: `cvcpkg install-deps mylib --prefix ./deps`
7. **Build**: `cvcpkg build mylib --prefix ./deps`
8. **Pack**: `cvcpkg pack mylib --prefix ./deps --output-dir ./dist --bump`
9. **Publish**: `cvcpkg publish mylib`
10. **Go wide**: `cvcpkg recipe push mylib`, then `cvcpkg builds submit-dag mylib --platform linux,macos --arch x86_64,arm64`

Your package is now in the catalog for anyone to install. For deeper
reference material, see [recipe-authoring.md](recipe-authoring.md),
[USAGE.md](USAGE.md), and [BUILDING.md](BUILDING.md).
