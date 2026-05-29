# cvcpkg

Component package manager for **libcvc-deps** prebuilt dependency bundles.

`cvcpkg` resolves a set of component requirements against the libcvc-deps
bundle catalog, downloads the matching archives, verifies their integrity,
and materializes a single `CMAKE_PREFIX_PATH`-compatible install prefix.

**Downstream projects should adopt `cvcpkg` instead of manually downloading
and extracting the monolithic libcvc-deps archive.** Per-component bundles
are smaller, cacheable, and version-locked — you only pull what you need.

## Quick start

```bash
# Install from PyPI (once published):
pipx install cvcpkg

# Or install from source:
cd tools/cvcpkg && pip install -e '.[progress]'

# List available components:
cvcpkg list --available

# Install specific components into a prefix:
cvcpkg install --prefix ./deps boost hdf5 fftw3

# Install from a requirements file:
cvcpkg install --from cvc-requirements.yaml --prefix ./deps

# Verify an existing prefix:
cvcpkg verify --prefix ./deps
```

## Integrating your downstream project

### Step 1: Create `cvc-requirements.yaml`

Place this file in your project root (e.g. alongside `CMakeLists.txt`):

```yaml
# cvc-requirements.yaml — declare which libcvc-deps components you need.
#
# cvcpkg resolves these against the published catalog and installs
# exactly the matching per-component bundles for your platform.

platform: auto          # auto-detect, or: linux | macos | windows
arch: auto              # auto-detect, or: x86_64 | arm64
config: release         # release | debug
link: shared            # shared | static

# Pin the libcvc-deps release to consume bundles from:
libcvc-deps: ">=1.2.0"

# Components your project needs — only these are downloaded:
components:
  - boost
  - hdf5
  - fftw3
  - tiff
  - vtk
  - qt6
```

### Step 2: Install dependencies

```bash
# Resolve, download, verify, and install into ./deps:
cvcpkg install --from cvc-requirements.yaml --prefix ./deps

# Or specify overrides on the command line:
cvcpkg install --from cvc-requirements.yaml --prefix ./deps \
  --config debug --link static
```

### Step 3: Point CMake at the prefix

```bash
cmake -S . -B build -DCMAKE_PREFIX_PATH="$(pwd)/deps"
```

All `find_package()` calls (Boost, HDF5, FFTW3, VTK, Qt6, etc.) will
resolve from the cvcpkg-managed prefix.

### Step 4: Lock for CI reproducibility

After a successful install, cvcpkg writes a lockfile:

```bash
# Commit this for reproducible CI builds:
git add cvcpkg.lock.yaml
```

Re-running `cvcpkg install` with a lockfile present replays the exact
same downloads (same SHA-256 digests), regardless of catalog updates.

### Example: CMake preset integration

```json
{
  "configurePresets": [{
    "name": "default",
    "cacheVariables": {
      "CMAKE_PREFIX_PATH": "${sourceDir}/deps"
    }
  }]
}
```

### Example: CI workflow

```yaml
- name: Install libcvc-deps
  run: |
    pip install cvcpkg
    cvcpkg install --from cvc-requirements.yaml --prefix ./deps

- name: Configure
  run: cmake -S . -B build -DCMAKE_PREFIX_PATH=${{ github.workspace }}/deps
```

## Publishing a downstream package

If you maintain a library that other CVC projects depend on, you can
publish it to the cvcpkg server so consumers can pull it with
`cvcpkg install`.

### Step 1: Write a recipe

Create `recipes/<your-package>/recipe.yaml`:

```yaml
name: my-library
upstream_version: "2.1.0"
cvc_revision: 1
description: "My library for CVC downstream consumers"

source:
  url: "https://github.com/org/my-library/archive/refs/tags/v2.1.0.tar.gz"
  sha256: "<sha256-of-tarball>"

build_matrix:
  - platform: linux
    arch: x86_64
  - platform: macos
    arch: arm64
  - platform: windows
    arch: x86_64

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
  - name: hdf5
    version: ">=1.10"

cmake_packages:
  - name: MyLibrary
    targets: ["MyLibrary::MyLibrary"]
```

### Step 2: Build the package

```bash
# Build using the recipe (fetches source, runs cmake, stages output):
cvcpkg build my-library --prefix ./stage \
  --config release --link shared

# Or if you already have a built install tree:
cvcpkg push ./stage --recipe recipes/my-library \
  --platform linux --arch x86_64 --config release --link shared
```

### Step 3: Publish to the server

```bash
# Get a publisher token from your server admin:
export CVCPKG_TOKEN="cvctok_..."

# Push to the cvcpkg server:
cvcpkg push ./stage --recipe recipes/my-library \
  --platform linux --arch x86_64 --config release --link shared \
  --server https://cvcpkg.example.org
```

### Server administration

```bash
# Install with server extras:
pip install cvcpkg[server]

# Start the server:
cvcpkg-server run --state-dir /var/lib/cvcpkg --host 0.0.0.0 --port 8080

# Create tokens:
cvcpkg-server token create --name ci-publisher --role publisher
cvcpkg-server token create --name dev-reader --role reader

# View audit log:
cvcpkg-server audit log --last 20
cvcpkg-server audit verify
```

---

## Authentication and Authorization

cvcpkg-server uses a **token-based RBAC** (role-based access control)
system.  Every mutating API call requires a bearer token; read-only
endpoints are unauthenticated by default but can be locked down.

### Token lifecycle

Tokens are issued by an admin and shown **exactly once** at creation
time.  Only an HMAC-SHA256 hash of the token is persisted on the
server — the raw secret is never stored.

```bash
# Create a publisher token (admin only):
cvcpkg-server token create --name ci-publisher --role publisher

# Create a reader token with 90-day expiry:
cvcpkg-server token create --name dev-reader --role reader \
  --expires-in-days 90
```

The raw token looks like `cvctok_<base64url>`.  Store it securely
(e.g. in a CI secret) and pass it via the `CVCPKG_TOKEN` environment
variable or `Authorization: Bearer <token>` header.

### Roles

| Role        | Permissions                                                  |
|-------------|--------------------------------------------------------------|
| `reader`    | Query catalog, list packages, download archives              |
| `publisher` | All reader permissions plus publish packages, yank versions  |
| `admin`     | All permissions: publish, yank, unyank, delete, manage tokens, view audit log |

### Revoking tokens

```bash
# Revoke immediately (admin only):
cvcpkg-server token revoke --name ci-publisher

# List all tokens:
cvcpkg-server token list
```

Revoked tokens are rejected on the next API call — no restart needed.

### Locking down reads

By default, `GET /v1/catalog`, `GET /v1/packages`, and
`GET /v1/download/{filename}` are public.  To require authentication
for all endpoints, start the server with:

```bash
cvcpkg-server run --state-dir /var/lib/cvcpkg --require-auth-for-reads
```

---

## Package Serving

### How the catalog works

cvcpkg-server maintains an `index.yaml` file in `--state-dir` that
lists every published bundle (name, version, platform, arch,
build_type, link, SHA-256 digest, archive URL, and optional signature
metadata).  The index revision increments on each publish/yank/delete.

Clients call `GET /v1/catalog` to receive the full bundle list, then
`GET /v1/download/{filename}` to fetch individual archives.

### API endpoints

| Method | Path                                   | Auth          | Description                        |
|--------|----------------------------------------|---------------|------------------------------------|
| GET    | `/healthz`                             | none          | Server health + uptime             |
| GET    | `/v1/catalog`                          | reader/public | Full bundle catalog                |
| GET    | `/v1/packages`                         | reader/public | Paginated package listing          |
| GET    | `/v1/packages/{name}`                  | reader/public | Versions of a specific component   |
| GET    | `/v1/download/{filename}`              | reader/public | Download an archive                |
| POST   | `/v1/publish`                          | publisher     | Upload a new bundle                |
| POST   | `/v1/packages/{name}/{version}/yank`   | publisher     | Yank a version (soft delete)       |
| POST   | `/v1/packages/{name}/{version}/unyank` | admin         | Restore a yanked version           |
| DELETE | `/v1/packages/{name}/{version}`        | admin         | Permanently delete a version       |
| POST   | `/v1/tokens`                           | admin         | Create a new API token             |
| DELETE | `/v1/tokens/{name}`                    | admin         | Revoke a token                     |
| GET    | `/v1/tokens`                           | admin         | List all tokens                    |
| GET    | `/v1/audit`                            | admin         | Paginated audit log                |
| GET    | `/v1/audit/verify`                     | admin         | Verify audit chain integrity       |

### SHA-256 integrity

Every archive receives a SHA-256 digest at publish time, recorded in
the catalog.  `cvcpkg install` verifies the digest after download
before extracting — a mismatch aborts the install.

---

## Publishing Packages

### Quick publish flow

```bash
# 1. Build a component from recipe:
cvcpkg build zlib --prefix ./stage \
  --config release --link shared

# 2. Pack to an archive:
cvcpkg pack zlib --prefix ./stage \
  --config release --link shared

# 3. Publish to server:
export CVCPKG_TOKEN="cvctok_..."
cvcpkg push ./stage --recipe recipes/zlib \
  --platform linux --arch x86_64 --config release --link shared \
  --server https://cvcpkg.example.org
```

### Signed publishing

To attach a cryptographic signature at publish time, first generate
a signing key (see [Package Signing](#package-signing) below), then
pass `--signing-key` during pack:

```bash
cvcpkg pack zlib --prefix ./stage \
  --config release --link shared \
  --signing-key ~/.config/cvcpkg/keys/release.key
```

The resulting archive will have a `.sig` sidecar file.  When the
archive is published to cvcpkg-server, the signature and key
fingerprint are stored in the catalog so consumers can verify.

### Yanking vs. deleting

**Yanking** is a soft delete: the archive stays on disk but `cvcpkg
install` will skip yanked versions (unless the lockfile pins one).
Only admins can **unyank**.

**Deleting** permanently removes the catalog entry.  Use with care —
consumers that pinned the deleted version will get download errors.

---

## Versioning and Revisions

### Version string format

Every published package has a version string of the form:

```
<upstream_version>+cvc.<cvc_revision>
```

For example, `1.86.0+cvc.1` means upstream Boost 1.86.0, CVC recipe
revision 1.  The `+cvc.N` suffix is SemVer build metadata — it is
ignored for range comparisons but used as a tiebreaker by the
resolver when multiple builds of the same upstream version exist.

The `cvc_revision` field in `recipe.yaml` controls the suffix:

```yaml
name: boost
upstream_version: "1.86.0"
cvc_revision: 1      # → published as 1.86.0+cvc.1
```

### Duplicate detection (publish conflicts)

The server rejects a publish with **HTTP 409 Conflict** if a package
with the same 6-field key already exists:

```
(name, version, platform, arch, build_type, link)
```

The error message is:

> `"{name}=={version} (...) already published.  Yank the existing
> version first, or use a new revision."`

Because the `version` field includes the `+cvc.N` suffix, bumping
`cvc_revision` produces a different version string and is **not**
considered a duplicate.  This is the intended mechanism for
re-publishing a corrected build of the same upstream version.

Note: yanking alone is **not** sufficient to re-publish — the
duplicate check does not filter yanked entries.  To re-publish the
exact same version string, an admin must **delete** the old entry
first.

### Bumping revisions with `rev-bump`

When a recipe needs a rebuild (patch fix, build script change,
dependency update), bump its `cvc_revision`:

```bash
# Bump zlib and all downstream dependents:
cvcpkg rev-bump zlib

# Output:
#   zlib: cvc_revision 1 → 2
#   hdf5: cvc_revision 3 → 4
#   vtk:  cvc_revision 1 → 2
```

The `--cascade` flag (default: on) automatically bumps every recipe
that transitively depends on the target.  This ensures the entire
dependency chain is rebuilt and re-published against the patched
version, catching breakage early rather than shipping an inconsistent
set of binaries.

**Why cascade?**  If a patch to `openssl` fixes a security issue,
every library linked against it (e.g. `grpc`, `protobuf`, `qt6`)
must be rebuilt to pick up the fix.  Publishing only the patched
`openssl` without rebuilding downstream would leave consumers with
binaries linked against the old, vulnerable version.  The cascade
ensures that either the full stack builds cleanly or the patch author
is forced to fix downstream breakage before publishing.

After bumping, the typical workflow is:

```bash
# 1. Bump revisions (edits recipe.yaml files in-place):
cvcpkg rev-bump openssl

# 2. Commit the bumped recipes:
git add recipes/ && git commit -m "rev-bump openssl + downstream"

# 3. Tag and push — CI rebuilds and publishes everything:
git tag v1.6.1 && git push origin v1.6.1
```

### Revision vs. version vs. catalog revision

| Term | Scope | Example | Purpose |
|------|-------|---------|---------|
| `upstream_version` | Recipe | `1.86.0` | The third-party project's own version |
| `cvc_revision` | Recipe | `3` | Rebuild counter for CVC-specific patches or build fixes |
| `version` (full) | Published package | `1.86.0+cvc.3` | Uniquely identifies this build in the catalog |
| Catalog `revision` | Server index | `42` | Monotonic counter incremented on each publish/yank/delete; used by clients to detect catalog staleness |

---

## Package Signing

cvcpkg supports **Ed25519 package signing** for publisher identity
verification.  The `cryptography` package is a required dependency
and is installed automatically with cvcpkg.

### Key management

Keys are stored in `~/.config/cvcpkg/keys/` (or
`$XDG_CONFIG_HOME/cvcpkg/keys/`) with three files per identity:

| File              | Contents                                        |
|-------------------|-------------------------------------------------|
| `<label>.key`     | PEM-encoded Ed25519 private key (mode 0600)     |
| `<label>.pub`     | PEM-encoded Ed25519 public key                  |
| `<label>.fp`      | SHA-256 fingerprint of the raw 32-byte public key (hex) |

#### Generate a keypair

```bash
cvcpkg key generate --label release

# Output:
# Generated key 'release'
#   Fingerprint: a1b2c3d4e5f6...
#   Private key: /home/user/.config/cvcpkg/keys/release.key
#   Public key:  /home/user/.config/cvcpkg/keys/release.pub
```

Optionally password-protect the private key:

```bash
cvcpkg key generate --label release --password "s3cret"
```

#### List keys

```bash
cvcpkg key list

# Output:
#   release              a1b2c3d4e5f67890…  (private+public)
#   upstream-qt          f0e1d2c3b4a59687…  (public only)
```

#### Import a publisher's public key

When a trusted publisher shares their public key, import it to
enable signature verification:

```bash
cvcpkg key import publisher-release.pub --label upstream
# Imported 'upstream' (f0e1d2c3b4a5…)
```

#### Export a public key

Share your public key with consumers:

```bash
cvcpkg key export --label release > release.pub
```

### Signing archives

#### Sign during pack

The easiest way: pass `--signing-key` to `cvcpkg pack` or
`cvcpkg pack-all` and the archive is signed automatically:

```bash
cvcpkg pack zlib --prefix ./stage \
  --config release --link shared \
  --signing-key ~/.config/cvcpkg/keys/release.key
```

This creates the archive **and** a `.sig` sidecar file.

#### Sign an existing archive

```bash
cvcpkg sign dist/zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz \
  --signing-key ~/.config/cvcpkg/keys/release.key

# Output:
# Signed: zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz.sig
#   (key: a1b2c3d4e5f6…)
```

### Signature format

Signatures are stored in `.sig` YAML sidecar files:

```yaml
signature: <base64url-encoded 64-byte Ed25519 signature>
key_fingerprint: <SHA-256 hex of the 32-byte Ed25519 public key>
```

The signature covers the **SHA-256 digest** of the archive contents
(not the raw file bytes directly), providing a standard
digest-then-sign construction.

### Verifying signatures

#### Verify a single archive

```bash
cvcpkg verify-sig dist/zlib-1.3.1+cvc.1-linux-x86_64-release-shared.tar.gz

# Output:
# Verified: signed by 'release' (a1b2c3d4e5f6…)
```

The command looks for `<archive>.sig` by default, or use
`--sig-file` to specify a different path.

#### Verify during install

Pass `--verify-signatures` to `cvcpkg install` to verify every
downloaded archive before extraction:

```bash
cvcpkg install --prefix ./deps --verify-signatures boost hdf5 zlib
```

If a package in the catalog has a signature and the matching public
key is in your keyring, verification happens automatically.  If the
signature is invalid or the signing key is not trusted, installation
aborts with a clear error.

### Trust model

1. **Key generation**: Each publisher generates their own Ed25519
   keypair with `cvcpkg key generate`.

2. **Key distribution**: The publisher shares their `.pub` file
   out-of-band (e.g. committed to the repo, posted on a website,
   or exchanged directly).

3. **Key import**: Consumers import the publisher's public key with
   `cvcpkg key import`.

4. **Verification**: When `--verify-signatures` is enabled,
   cvcpkg checks the archive's signature against the local keyring.
   It first tries the key whose fingerprint matches the catalog
   entry, then falls back to trying all trusted keys (to support
   key rotation).

5. **Non-repudiation**: The server records the signature and key
   fingerprint in the catalog at publish time, providing an audit
   trail of who signed each package.

### CI signing workflow

```yaml
- name: Sign and publish
  env:
    CVCPKG_TOKEN: ${{ secrets.CVCPKG_PUBLISHER_TOKEN }}
    SIGNING_KEY: ${{ secrets.SIGNING_PRIVATE_KEY }}
  run: |
    # Write the signing key from CI secrets:
    mkdir -p ~/.config/cvcpkg/keys
    echo "$SIGNING_KEY" > ~/.config/cvcpkg/keys/ci.key
    chmod 600 ~/.config/cvcpkg/keys/ci.key

    # Build, pack (with signature), and publish:
    cvcpkg build $COMPONENT --prefix ./stage
    cvcpkg pack $COMPONENT --prefix ./stage \
      --signing-key ~/.config/cvcpkg/keys/ci.key \
      --config release --link shared
```

---

## Audit Trail

cvcpkg-server maintains a tamper-evident, append-only audit log.
Every mutation (publish, yank, unyank, delete, token create, token
revoke) is recorded with:

- **Timestamp** (UTC)
- **Action** (the operation performed)
- **Actor** (the token name that performed it)
- **Target** (the component or token affected)
- **Detail** (platform, SHA-256, etc.)
- **Chain hash** (SHA-256 of the previous entry for tamper detection)

### Viewing the log

```bash
# Last 20 entries:
cvcpkg-server audit log --last 20

# Filter by action:
cvcpkg-server audit log --action publish

# Filter by target:
cvcpkg-server audit log --target "boost==1.86.0+cvc.1"
```

### Verifying integrity

```bash
cvcpkg-server audit verify

# Output (if intact):
# chain intact (142 entries)
```

The verify command walks the full chain and checks that each entry's
`prev_sha256` matches the hash of the preceding entry.  A broken
chain indicates tampering or data corruption.

---

## Build Directory Configuration

By default, cvcpkg creates intermediate build trees in the system temp
directory (`$TMPDIR`, `/tmp`, etc.).  For large builds this can exhaust
space on small temp partitions, or be slow on non-SSD storage.

Use **`--work-dir`** (or the **`CVCPKG_WORK_DIR`** environment variable)
to redirect build trees to a dedicated volume:

```bash
# Point builds at a fast NVMe scratch partition:
cvcpkg build-all --work-dir /mnt/scratch/cvcpkg-builds \
    --platform linux --config release --link shared

# Or set it globally via environment:
export CVCPKG_WORK_DIR=/mnt/scratch/cvcpkg-builds
cvcpkg pack-all --platform linux --config release --link shared
```

The directory is created automatically if it doesn't exist.  Each recipe
gets its own sub-directory under `--work-dir` (e.g.
`/mnt/scratch/cvcpkg-builds/cvcpkg-zlib-XXXXXXXX/`).

When `--work-dir` is not set, the default prefix directory for
`build-all` (when `--prefix` is also omitted) is likewise placed in the
system temp directory.

---

## Mirror Support

cvcpkg-server supports **mirror mode**, where a read-only replica
syncs its catalog from an upstream primary and proxies archive
downloads on demand.  Clients automatically discover healthy mirrors
and use them as fallback download sources.

### Setting up a mirror

Start a mirror server pointing at an upstream primary:

```bash
cvcpkg-server run \
    --mirror-mode \
    --mirror-upstream https://pkg.tx.wtf \
    --mirror-token cvctok_... \
    --database-url postgresql+asyncpg://user:pass@localhost/mirror_db \
    --state-dir ./mirror-data \
    --port 8421
```

| Flag | Env var | Description |
|------|---------|-------------|
| `--mirror-mode` | `CVCPKG_MIRROR_MODE` | Enable read-only mirror mode |
| `--mirror-upstream` | `CVCPKG_MIRROR_UPSTREAM` | Upstream server URL (required) |
| `--mirror-token` | `CVCPKG_MIRROR_TOKEN` | Token for upstream auth |
| `--mirror-sync-interval` | `CVCPKG_MIRROR_SYNC_INTERVAL` | Catalog sync interval in seconds (default: 3600) |

Mirror-mode servers reject publish and upload requests (HTTP 403) and
periodically sync the catalog from the upstream.  Archive files are
fetched on first request and cached locally.

### Registering mirrors with the primary

Mirrors register themselves with the primary so clients can discover
them:

```bash
curl -X POST https://pkg.tx.wtf/v1/mirrors/register \
  -H 'Content-Type: application/json' \
  -d '{"url": "https://eu.pkg.tx.wtf", "display_name": "EU Mirror", "contact": "ops@eu.pkg.tx.wtf"}'
```

The primary health-checks registered mirrors every 5 minutes.  After
3 consecutive failures a mirror is marked unhealthy and removed from
the client mirror list.  Re-registering clears rejection/unhealthy
state.

### Admin mirror management

```bash
# List all mirrors (admin-only, includes rejected/unhealthy)
curl -H "Authorization: Bearer $ADMIN_TOKEN" https://pkg.tx.wtf/v1/mirrors/all

# Reject a mirror
curl -X POST "https://pkg.tx.wtf/v1/mirrors/reject?url=https://bad.example.com" \
  -H "Authorization: Bearer $ADMIN_TOKEN"

# Permanently remove a mirror
curl -X DELETE "https://pkg.tx.wtf/v1/mirrors?url=https://old.example.com" \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### Client mirror failover

When `CVCPKG_SERVER_URL` is set, the `install` and `sync` commands
automatically fetch the mirror list from the server and inject mirror
URLs as fallback download sources.  If the primary download fails,
mirrors are tried in order.

```bash
export CVCPKG_SERVER_URL=https://pkg.tx.wtf
cvcpkg install --from cvc-requirements.yaml --prefix ./deps
```

### Downloading archives without installing

The `download` command fetches archives to a local directory without
extracting them:

```bash
# Download specific components
cvcpkg download zlib boost --output-dir ./archives

# With mirror failover
cvcpkg download zlib --server https://pkg.tx.wtf -o ./dist

# Pin a version
cvcpkg download zlib==1.3.1+cvc.1 -o ./dist --config debug
```

---

## Troubleshooting

### 502 errors during publish

When many CI runners publish archives concurrently (e.g. a tagged
release building 4 macOS configs × 16 packages), the cvcpkg-server
backend can run out of memory and restart, causing the reverse proxy
to return **502 Bad Gateway**.

**Checklist:**

1. **Container memory limit** — Ensure the backend container has
   enough memory for concurrent uploads.  In
   `docker-compose.production.yml`, set `deploy.resources.limits.memory`
   to at least 4–8 GB for production workloads with many concurrent
   publishers.

2. **Reverse proxy body limit** — If using Apache, the
   `LimitRequestBody` directive must be large enough for the biggest
   archive (e.g. emsdk at ~840 MB).  Set it to at least 1.1 GB:
   ```
   LimitRequestBody 1153433600
   ```
   For nginx, use `client_max_body_size 1100m;`.

3. **Proxy timeout** — Large chunked uploads can take several minutes.
   Ensure your proxy timeout is at least 900 s (`ProxyTimeout 900` in
   Apache, `proxy_read_timeout 900s` in nginx).

**Symptoms:** Container restart count > 0 (`docker inspect <container>
--format '{{.RestartCount}}'`), 502 responses in the proxy access log
concentrated in a short time window.

---

## Development

```bash
cd tools/cvcpkg
pip install -e '.[progress,server]'
pytest
```

### Running with coverage

```bash
pytest --cov=cvcpkg --cov-branch --cov-report=html:htmlcov tests/
open htmlcov/index.html
```

Coverage reports are also generated as CI artifacts on every push/PR —
download them from the workflow run's Artifacts section.

## License

MIT — see [LICENSE](../../LICENSE).
