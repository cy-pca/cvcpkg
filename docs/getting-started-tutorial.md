# Getting Started with cvcpkg: Submitting Packages

A practical walkthrough for publishing your library to the cvcpkg server so others can install it with `cvcpkg install`.

---

## Prerequisites

- **Python 3.11+** (for installing the `cvcpkg` CLI)
- A **cvcpkg server** running (the public server at `https://cvcpkg.org`, or a self-hosted instance)
- An **API token** with `publisher` or `admin` role (ask your server admin, or create one via `POST /v1/tokens` if you have admin access)

## Step 1: Install the cvcpkg CLI

```bash
pip install cvcpkg
```

Verify it works:

```bash
cvcpkg --version
```

## Step 2: Authenticate

Set your API token so the CLI can communicate with the server:

```bash
export CVCPKG_TOKEN="cvctok_..."
```

Optionally point to a custom server (default is `https://cvcpkg.org`):

```bash
export CVCPKG_SERVER_URL="https://cvcpkg.org"
```

Test the connection:

```bash
cvcpkg catalog --server https://cvcpkg.org
```

This lists all packages available in the catalog.

## Step 3: Write a Recipe

Create a `recipe.yaml` for your package. A recipe tells cvcpkg how to build and package your library.

```yaml
name: my-library
upstream_version: "2.1.0"
cvc_revision: 1
description: "My library for downstream consumers"

source:
  url: "https://github.com/org/my-library/archive/refs/tags/v2.1.0.tar.gz"
  sha256: "<sha256-of-tarball>"

build_matrix:
  - platform: linux
    arch: x86_64
  - platform: macos
    arch: arm64

build:
  system: cmake
  args:
    - "-DCMAKE_BUILD_TYPE={{config}}"
    - "-DBUILD_SHARED_LIBS={{shared}}"
    - "-DCMAKE_INSTALL_PREFIX={{prefix}}"

package:
  files:
    - "lib/**"
    - "include/**"
    - "share/**/cmake/**"
    - "bin/**"

dependencies:
  - name: boost
    version: ">=1.83"

cmake_packages:
  - name: MyLibrary
    targets: ["MyLibrary::MyLibrary"]
```

### Key Fields

| Field | Description |
|---|---|
| `name` | Package name (lowercase, kebab-case) |
| `upstream_version` | The upstream library version |
| `cvc_revision` | Your revision number. Bump this when you change the recipe (e.g., fix a build arg). The final catalog version will be `{upstream_version}+cvc.{cvc_revision}`. |
| `source.url` | Direct URL to the source tarball or archive |
| `source.sha256` | SHA-256 hash of the source archive for integrity verification |
| `build_matrix` | List of platform/arch combinations to build for |
| `build.system` | Build system to use (`cmake`, `make`, `meson`, `script`, etc.) |
| `build.args` | Arguments passed to the build system. Template variables like `{{config}}` and `{{shared}}` are expanded at build time. |
| `package.files` | Glob patterns for files to include in the final `.tar.zst` package archive |
| `dependencies` | Other cvcpkg packages this package depends on, with optional version constraints |
| `cmake_packages` | CMake package information so downstream projects can find and use this library via `find_package()` |

### Build Templates

The following template variables are available in `build.args`:

- `{{config}}` - build configuration (`release`, `debug`, etc.)
- `{{shared}}` - evaluates to `ON` for shared builds, `OFF` for static
- `{{prefix}}` - the install prefix path

### Platform-Archive Packages

For platform-independent packages (data bundles, headers-only libraries, etc.), use `platform: any`:

```yaml
build_matrix:
  - platform: any
```

## Step 4: Compute the Source SHA-256

```bash
curl -sL "https://github.com/org/my-library/archive/refs/tags/v2.1.0.tar.gz" \
  | sha256sum
```

Paste the resulting hash into `source.sha256` in the recipe.

## Step 5: Build Locally (Optional)

Before publishing, verify the recipe builds locally:

```bash
cvcpkg build my-library --prefix ./stage \
  --config release --link shared
```

This fetches the source, runs the build, and stages the output into `./stage`.

## Step 6: Pack and Publish

Pack the built output into a distributable archive:

```bash
cvcpkg pack my-library --prefix ./stage \
  --config release --link shared
```

Push the recipe and package to the server:

```bash
cvcpkg recipes push my-library
cvcpkg publish my-library
```

Or, in a single step (build + pack + publish):

```bash
cvcpkg build my-library --prefix ./stage \
  --config release --link shared && \
cvcpkg pack my-library --prefix ./stage \
  --config release --link shared && \
cvcpkg publish my-library
```

## Step 7: Remote Builds (Optional)

For packages that require building on multiple platforms, use the remote builder system. Instead of building locally, submit a build job to the server and let a builder agent handle it:

```bash
cvcpkg remote-build submit my-library
```

Track the build progress:

```bash
cvcpkg remote-build follow-dag <dag_id>
```

## Step 8: Verify Installation

Once published, anyone can install your package:

```bash
cvcpkg install my-library --prefix ./deps
```

Or pin a specific version:

```bash
cvcpkg install "my-library==2.1.0+cvc.1" --prefix ./deps
```

## Revision Bumping

When you need to rebuild with a recipe change (e.g., a CMake flag fix) without changing the upstream version, bump `cvc_revision`:

```bash
# Automatically bump the revision:
cvcpkg rev-bump my-library
```

This increments `cvc_revision` and updates the catalog entry.

## Pushing Recipes

To make a recipe available on the server for others to discover and build:

```bash
cvcpkg recipes push my-library
```

This uploads the recipe definition to the server so builders can fetch it and consumers can reference it in dependency declarations.

## Publishing to an Organization (Optional)

If you are part of an organization, you can publish packages under the org
namespace by adding `--org`:

```bash
cvcpkg publish my-library --org my-team
cvcpkg recipes push my-library --org my-team
```

This requires your token to belong to a member of the org. The package will
appear in the catalog under `my-team/my-library`.

To install an org-scoped package:

```bash
cvcpkg install my-library --org my-team --prefix ./deps
```

Private org packages require authentication — set `CVCPKG_TOKEN` before
installing.

See the [Organizations guide](organizations.md) for full details on creating
orgs, managing members, and private organizations.

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `401 Unauthorized` | Token missing or expired | Set `CVCPKG_TOKEN` env var |
| `403 Forbidden` | Token lacks `publisher` role | Ask admin to upgrade your token |
| `409 Conflict` | Package already published at this version | Bump `cvc_revision` and republish |
| `502 Bad Gateway` | Server backend unavailable | Check server health at `/healthz` |

---

## Summary

1. **Install** the CLI: `pip install cvcpkg`
2. **Authenticate**: export `CVCPKG_TOKEN`
3. **Write** a `recipe.yaml` with build instructions and source URL
4. **Compute** the source tarball SHA-256
5. **Build** locally to verify: `cvcpkg build`
6. **Pack** the output: `cvcpkg pack`
7. **Publish** to the server: `cvcpkg publish`
8. **Push** the recipe: `cvcpkg recipes push`

Your package is now available in the catalog for anyone to install.
