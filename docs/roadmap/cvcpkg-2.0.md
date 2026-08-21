# Roadmap: cvcpkg 2.0 — production registry with trust & identity

Status: **Implemented** — historical founding design (drafted 2026-05);
shipped as v2.0.0 (the [CHANGELOG](../../CHANGELOG.md) v2.0.0 entry cites
this document). Superseded for live tracking by the sibling
[CVCPKG-ROADMAP.md](CVCPKG-ROADMAP.md).
Author: roadmap drafted 2026-05-22.
Target: libcvc-deps v2.0.0 (with possible 1.x releases working up to it).

---

## 1. Motivation

cvcpkg 1.x proved the recipe-driven component model: 25 recipes, a
FastAPI server, storage backends, per-component bundles, and a
lockfile-based install flow.  However, the entire stack is still
GitHub-centric — catalogs live on GitHub Pages, archives attach to
GitHub Releases, and there is no public registration path for
third-party publishers.

v2.0 makes the **cvcpkg daemon the authoritative registry** for
package catalogs, decoupled from any single hosting provider.  It adds
a proper database, multi-backend package mirroring, public publisher
registration with admin review, cryptographic identity for publishers,
and supply-chain protections for package names.

### Goals

| # | Goal |
|---|------|
| G1 | The daemon is the source of truth for the catalog; GitHub is one mirror among many. |
| G2 | Packages can be fetched from any configured backend (GitHub Releases, S3, SFTP, arbitrary HTTPS, local disk). The daemon tries each in order. |
| G3 | The public can request credentials to publish packages; admins review and approve. |
| G4 | Package names are reserved upon first publish and bound to a verified publisher identity. Admins can block or transfer name ownership. |
| G5 | Every published artifact is signed by the publisher's key; the daemon and clients verify signatures before accepting. |
| G6 | A public publisher directory lets anyone query who owns a package name and inspect their signing key. |
| G7 | The daemon uses a database (SQLite/MySQL/PostgreSQL via SQLModel) instead of YAML files. |
| G8 | Expand platform coverage beyond Linux/macOS/Windows to BSDs, Haiku, and other OSes, and beyond x86_64/arm64 to RISC-V, MIPS, POWER, etc. Self-hosted and cross-compilation runners populate the catalog for all targets. |

### Non-goals

- Replacing vcpkg, conan, or spack.  cvcpkg serves the CVC/libcvc
  ecosystem only.
- Running a CDN.  The daemon proxies or redirects to storage backends;
  it is not itself a high-throughput file server.
- Automatic trust.  Every new publisher goes through human review.
- **Requiring the daemon**.  The CLI must remain fully functional
  without a running server — building from recipes, publishing to
  a local directory, and installing from a filesystem catalog are
  all first-class workflows that never touch the network.

---

## 2. Architecture overview

cvcpkg operates in three tiers.  Every higher tier is optional —
the CLI always works in the tier below it.

```
┌─────────────────────────────────────────────────────────────────────┐
│ Tier 0 — Local only (no network, no daemon)                       │
│                                                                   │
│  recipes/       cvcpkg build-all ──► prefix/   (build from source)│
│  recipes/       cvcpkg pack-all  ──► dist/     (create archives)  │
│  dist/          cvcpkg push dist/ --dest ./repo  (publish to dir) │
│  ./repo         cvcpkg install --catalog ./repo  (install from it)│
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ Tier 1 — Remote storage, no daemon                                │
│                                                                   │
│  cvcpkg push dist/ --dest s3://bucket/path                        │
│  cvcpkg push dist/ --dest sftp://host/path                        │
│  cvcpkg push dist/ --dest gh-release://transfix/libcvc-deps/v1.3.0│
│  cvcpkg install --catalog https://example.org/catalog/latest.yaml │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│ Tier 2 — Daemon (production registry)                             │
│                                                                   │
│  cvcpkg CLI ────────────► cvcpkg-server 2.0 (FastAPI)             │
│  (install / publish)       ├─ SQLModel DB (SQLite/PG/MySQL)       │
│                            ├─ Backend Router                      │
│                            │   ├─ S3                              │
│                            │   ├─ SFTP                            │
│                            │   ├─ HTTPS mirror                    │
│                            │   ├─ GitHub Releases (transitional)  │
│                            │   └─ Local disk                      │
│                            ├─ Signature Verify (Ed25519)          │
│                            └─ Publisher Directory                 │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 3. Local-first operation (Tier 0 & Tier 1)

The daemon is the crown jewel of v2.0, but cvcpkg must **never**
require it.  A researcher on a laptop, a CI runner in an air-gapped
lab, or a developer iterating on a recipe must be able to build,
publish, and install without touching the network.

### 3.1. Filesystem catalog

A **filesystem catalog** is a directory with a well-known layout:

```
repo/
  catalog.yaml          # index of all packages in this repo
  archives/
    boost-1.83.0-cvc1-linux-x86_64-release-shared.tar.gz
    boost-1.83.0-cvc1-linux-x86_64-release-shared.tar.gz.sig
    hdf5-1.10.10-cvc1-linux-x86_64-release-shared.tar.gz
    ...
```

`catalog.yaml` has the same schema as the daemon's catalog response
(schema_version, revision, bundles list).  The CLI reads it directly;
no HTTP involved.

### 3.2. Publishing to a directory

```bash
# Build from recipes:
cvcpkg build-all --platform linux --config release --link shared \
  --prefix ./prefix --recipes-dir recipes

# Pack into per-component archives:
cvcpkg pack-all --prefix ./prefix --recipes-dir recipes --dist ./dist

# Publish to a local repo directory:
cvcpkg push ./dist/*.tar.gz --dest ./repo
```

`cvcpkg push --dest <dir>` copies archives into `<dir>/archives/`,
regenerates `<dir>/catalog.yaml`, and (if the publisher has a local
Ed25519 key) writes detached `.sig` files.  The result is a
self-contained repo that can be:

- Shared over NFS, USB, or `rsync`.
- Served by any static HTTP server (`python -m http.server`).
- Pointed at by `cvcpkg install --catalog ./repo`.
- Imported into a running daemon via `cvcpkg-server import ./repo`.

### 3.3. Installing from a directory

```bash
# Install directly from a local repo:
cvcpkg install --catalog ./repo --prefix ./deps boost hdf5 fftw3

# Or via a requirements file:
cvcpkg install --from cvc-requirements.yaml --prefix ./deps \
  --catalog ./repo
```

The `--catalog` flag accepts:
- A local directory path (Tier 0).
- A `file://` URI.
- An `https://` URL pointing to `catalog.yaml` (Tier 1).
- An `s3://`, `sftp://`, or `gh-release://` URI (Tier 1).
- A daemon URL like `https://pkg.cvc.example.org` (Tier 2).

When no `--catalog` is given, the CLI checks (in order):
1. `catalog` key in the project's `cvc-requirements.yaml`.
2. `catalog` key in `~/.config/cvcpkg/config.yaml`.
3. The compiled-in default URL (currently GitHub Pages).

### 3.4. Recipe-only workflow (no archives at all)

For maximum reproducibility, a project can vendor its full recipe
set and build everything from source:

```bash
cvcpkg build-all --recipes-dir ./vendored-recipes \
  --platform linux --config release --link shared \
  --prefix ./deps
cmake -S . -B build -DCMAKE_PREFIX_PATH=$(pwd)/deps
```

This never fetches an archive, never contacts a server, and produces
a deterministic prefix from pinned source tarballs.  This workflow
is unchanged from 1.x and will remain supported in all future
versions.

---

## 4. Multi-platform & multi-OS expansion

### 4.1. Current coverage (v1.3)

| OS | Arch | Runner |
|----|------|--------|
| Linux (Ubuntu 24.04) | x86_64 | `ubuntu-latest` (GitHub-hosted) |
| macOS (Sonoma+) | arm64 | `macos-latest` (GitHub-hosted) |
| Windows (Server 2022+) | x86_64 | `windows-latest` (GitHub-hosted) |

### 4.2. Target coverage (v2.x)

| OS | Arch | Runner strategy |
|----|------|----------------|
| Linux (glibc) | x86_64 | GitHub-hosted |
| Linux (glibc) | aarch64 / arm64 | GitHub `ubuntu-24.04-arm` or self-hosted |
| Linux (glibc) | riscv64 | Self-hosted or QEMU cross-build |
| Linux (musl / Alpine) | x86_64, aarch64 | Docker `alpine:latest` on GitHub-hosted |
| macOS | arm64 | GitHub-hosted |
| macOS | x86_64 | GitHub-hosted (Rosetta or Intel runner) |
| Windows | x86_64 | GitHub-hosted |
| Windows | arm64 | Self-hosted (Snapdragon Dev Kit or Azure arm64 VM) |
| FreeBSD | x86_64, aarch64 | Self-hosted jail or `cross-platform-actions/action` |
| NetBSD | x86_64, aarch64 | Self-hosted VM or `cross-platform-actions/action` |
| OpenBSD | x86_64 | Self-hosted VM or `cross-platform-actions/action` |
| HaikuOS | x86_64 | Self-hosted VM |
| Linux (glibc) | ppc64le | Self-hosted or IBM Cloud |
| Linux (glibc) | s390x | Self-hosted or IBM Cloud |
| Linux (glibc) | mips64el | Self-hosted or QEMU cross-build |

### 4.3. Platform / arch identifiers

The `platform` and `arch` fields in recipes, catalogs, and the
database are free-form strings (not enums) to allow adding new
targets without schema changes.  Canonical values:

**Platforms**: `linux`, `macos`, `windows`, `freebsd`, `netbsd`,
`openbsd`, `haiku`, `linux-musl`

**Architectures**: `x86_64`, `arm64`, `riscv64`, `ppc64le`,
`s390x`, `mips64el`, `armv7l`, `i686`

`cvcpkg platform.py` will grow to detect these from `sys.platform`
and `platform.machine()`, with an `--platform` / `--arch` CLI
override for cross-compilation scenarios.

### 4.4. Runner infrastructure

**GitHub-hosted runners** cover Linux x86_64/arm64, macOS arm64,
and Windows x86_64.  Everything else requires either:

1. **Self-hosted runners**: physical or cloud VMs running the target
   OS natively.  We register them with GitHub Actions using labels
   like `freebsd-x86_64`, `haiku-x86_64`, `riscv64-linux`.  Recipes
   build in a native shell.

2. **Cross-compilation via QEMU/Docker**: for architectures where
   native hardware is scarce (RISC-V, MIPS, POWER), we can use
   QEMU user-mode emulation inside Docker on a GitHub-hosted x86_64
   runner.  This is slower but requires no dedicated hardware.

3. **`cross-platform-actions/action`**: a GitHub Action that boots
   FreeBSD, NetBSD, or OpenBSD in a VM on a GitHub-hosted runner
   (uses QEMU under the hood).  Good for CI validation; may be too
   slow for full recipe builds.

4. **Dedicated build servers**: for high-frequency targets (FreeBSD
   arm64, Linux riscv64), we provision persistent VMs with the
   GitHub Actions runner agent and a warm build cache.

### 4.5. Recipe portability

Recipes must declare which platforms they support via
`build_matrix[].platform`.  Recipes that use platform-specific
build scripts (`build.sh`, `build.ps1`) need per-platform variants.
The recipe schema already supports this via conditional deps:

```yaml
build_matrix:
  - platform: linux
    arch: x86_64
  - platform: linux
    arch: arm64
  - platform: linux
    arch: riscv64
  - platform: freebsd
    arch: x86_64
  - platform: haiku
    arch: x86_64

build:
  system: cmake
  args:
    - "-DCMAKE_BUILD_TYPE={{config}}"
    - "-DBUILD_SHARED_LIBS={{shared}}"
```

Recipes that rely on system package managers (`apt`, `brew`, `vcpkg`)
will need BSD-specific equivalents (`pkg`, `pkgsrc`, `pkgin`).  The
`_common/env-*.sh` scripts will grow BSD and Haiku variants.

### 4.6. Catalog growth projection

With 25 components × 15 OS/arch combos × 2 configs × 2 link modes,
the catalog could grow to ~1500 bundles per release version.  This
reinforces the need for:

- Database-backed catalog (§6) instead of flat YAML.
- Multi-backend storage (§7) to distribute the load.
- Per-component bundles so consumers only download their target.

### 4.7. Testing strategy for exotic platforms

Not every recipe will build on every platform from day one.  The
approach:

1. **Tier A** (must pass CI): Linux x86_64/arm64, macOS arm64,
   Windows x86_64.  Build failures block the release.
2. **Tier B** (best-effort CI): FreeBSD x86_64, Linux riscv64,
   Linux musl x86_64.  Build failures are reported but don't block.
3. **Tier C** (community-contributed): NetBSD, OpenBSD, HaikuOS,
   MIPS, POWER.  Builds run on self-hosted runners when available.
   Failures are tracked in issues but don't block CI.

Tier promotion happens as recipes stabilize on a platform and
runner availability improves.

---

## 5. GitHub Releases as a transitional backend

GitHub Releases has served well as the initial distribution channel
for libcvc-deps bundles, but it has scaling limits:

| Constraint | Limit | Current usage |
|------------|-------|---------------|
| Individual asset size | 2 GB | Windows bundles already ≥2 GB |
| Total assets per release | Soft limit ~100 | v1.3.0 ships ~100 per-component archives × 4 platform/config combos |
| Release storage per repo | 10 GB (soft) | Growing with each tagged release |
| Download bandwidth | Generous but opaque | No SLA, no CDN control |
| API rate limits | 5000 req/hr authenticated | Sufficient for now |

The v2.0 plan treats GitHub Releases as **one backend among many**,
not the primary.  The migration path:

1. **v1.3.x** (now): GitHub Releases remains the primary backend.
   Per-component archives are published there by CI.
2. **v1.4.x**: Add S3 as a secondary/mirror backend alongside GitHub.
   CI uploads to both.  `cvcpkg install` prefers S3 but falls back
   to GitHub.
3. **v2.0**: The daemon's backend router takes over.  GitHub Releases
   becomes a fallback mirror.  New publishes go to S3/SFTP/local as
   the primary.  Old GitHub Release URLs continue to work via the
   `gh-release://` backend.
4. **v2.x+**: GitHub Releases can be retired once all lockfiles
   referencing those URIs have aged out.

Crucially, even as we move away from GitHub as the primary host,
**all existing lockfiles remain valid** — the backend router knows
how to resolve `gh-release://` URIs indefinitely.

---

## 6. Database layer (SQLModel)

### 6.1. Why SQLModel

The 1.x server stores tokens, audit entries, and the package index in
YAML files.  This works for a single-node lab deployment but cannot
support:

- Concurrent writes from multiple uvicorn workers.
- Indexed queries (packages by name, platform, publisher).
- Transactional integrity (publish + audit in one commit).
- Horizontal scaling behind a load balancer.

[SQLModel](https://sqlmodel.tiangolo.com/) sits on top of SQLAlchemy
and Pydantic, so the existing Pydantic models migrate almost
verbatim.  The backend is selected by a single `DATABASE_URL`
environment variable:

| Backend    | DATABASE_URL example                     | Use case               |
|------------|------------------------------------------|------------------------|
| SQLite     | `sqlite:///var/lib/cvcpkg/cvcpkg.db`     | Single-node, dev, CI   |
| PostgreSQL | `postgresql://user:pw@db:5432/cvcpkg`    | Production multi-node  |
| MySQL      | `mysql://user:pw@db:3306/cvcpkg`         | Enterprise deployments |

### 6.2. Data model

```
Publisher
  id              UUID PK
  username        UNIQUE
  display_name
  email
  public_key      TEXT (PEM-encoded Ed25519 public key)
  key_fingerprint TEXT (SHA-256 of public key bytes)
  status          ENUM(pending, active, suspended, revoked)
  created_at
  updated_at
  reviewed_by     FK → Publisher.id  (admin who approved)
  review_note     TEXT

PackageName
  name            PK
  owner_id        FK → Publisher.id
  created_at
  status          ENUM(active, reserved, blocked)
  blocked_reason  TEXT
  blocked_by      FK → Publisher.id  (admin)

PackageRelease
  id              UUID PK
  name            FK → PackageName.name
  version         TEXT
  platform        TEXT   (linux, macos, windows, freebsd, netbsd, openbsd, haiku, linux-musl, ...)
  arch            TEXT   (x86_64, arm64, riscv64, ppc64le, s390x, mips64el, armv7l, i686, ...)
  config          TEXT   (release | debug)
  link            TEXT   (shared | static)
  sha256          TEXT
  size_bytes      BIGINT
  signature       TEXT   (detached Ed25519 signature, base64)
  published_by    FK → Publisher.id
  published_at    DATETIME
  yanked          BOOL DEFAULT FALSE
  yanked_by       FK → Publisher.id
  yanked_at       DATETIME
  storage_uris    JSON   (list of backend URIs where the archive lives)
  UNIQUE(name, version, platform, arch, config, link)

Token
  id              UUID PK
  owner_id        FK → Publisher.id
  name            TEXT
  token_hash      TEXT
  role            ENUM(reader, publisher, admin)
  scopes          JSON   (list of package name patterns, e.g. ["my-lib", "my-*"])
  created_at
  expires_at
  revoked         BOOL DEFAULT FALSE
  revoked_by      FK → Publisher.id

AuditEntry
  id              BIGINT PK AUTO
  timestamp       DATETIME
  action          ENUM(...)
  actor_id        FK → Publisher.id
  target          TEXT
  detail          TEXT
  prev_hash       TEXT   (chained SHA-256 for tamper detection)

StorageBackend
  id              UUID PK
  scheme          TEXT (s3, sftp, https, gh-release, local)
  uri_prefix      TEXT
  priority        INT  (lower = tried first)
  enabled         BOOL DEFAULT TRUE
  credentials_ref TEXT (vault path, env var name, or inline for dev)
  health_check_at DATETIME
  healthy         BOOL DEFAULT TRUE
```

### 6.3. Migration from 1.x

- A one-shot CLI command `cvcpkg-server migrate-from-v1 <state_dir>`
  reads the existing YAML files (tokens.yaml, audit.yaml, index.yaml)
  and imports them into the database.
- The v2 server refuses to start if it detects YAML-era files without
  a migration marker, printing clear instructions.

---

## 7. Multi-backend storage & mirroring

### 7.1. Backend router

The daemon maintains an ordered list of `StorageBackend` entries (from
the database).  On **download**, the router:

1. Queries `PackageRelease.storage_uris` for known locations.
2. Tries each URI in backend-priority order.
3. Returns a redirect (HTTP 302) or streams the bytes, depending on
   whether the backend supports direct client access.
4. If all fail, returns 502 with diagnostics.

On **publish**, the daemon:

1. Accepts the upload into a temporary staging area.
2. Verifies the publisher's Ed25519 signature (see §6).
3. Writes the archive to the **primary** backend.
4. Enqueues async replication to secondary backends (best-effort;
   failure is logged but does not block the publish response).
5. Records all successful URIs in `PackageRelease.storage_uris`.

### 7.2. Health checks

A background task (configurable interval, default 5 min) pings each
backend's `head()` on a sentinel object.  Unhealthy backends are
deprioritized until they recover.

### 7.3. Client-side fallback

The `cvcpkg install` client also has its own backend list (from
`~/.config/cvcpkg/config.yaml` mirrors).  If the daemon is
unreachable, the client can fall back to direct-from-mirror download
using the lockfile's `storage_uris` field.  This preserves offline /
air-gapped install capability.

---

## 8. Public publisher registration

### 8.1. Registration flow

```
User                           cvcpkg-server                   Admin
 │                                  │                            │
 │  POST /v2/publishers/register    │                            │
 │  { username, email, public_key } │                            │
 │ ────────────────────────────────►│                            │
 │                                  │  status = "pending"        │
 │  ◄─ 201 { id, status: pending } │                            │
 │                                  │                            │
 │                                  │  GET /v2/admin/publishers  │
 │                                  │◄───────────────────────────│
 │                                  │  ──► list of pending       │
 │                                  │                            │
 │                                  │  POST /v2/admin/publishers │
 │                                  │       /{id}/approve        │
 │                                  │◄───────────────────────────│
 │                                  │  status = "active"         │
 │                                  │                            │
 │  POST /v2/tokens                 │                            │
 │  (now allowed — identity active) │                            │
 │ ────────────────────────────────►│                            │
 │  ◄─ 201 { token: "cvctok_..." } │                            │
```

1. **Anyone** can call `POST /v2/publishers/register` with a username,
   email, and Ed25519 public key.  The server returns status `pending`.
2. An **admin** reviews the request via `GET /v2/admin/publishers?status=pending`.
   They can approve, reject (with reason), or request more info.
3. Once approved, the publisher can create tokens and publish packages.
4. Email verification (link or code) is optional but recommended;
   enforceability depends on deployment.  For CVC internal use, admin
   approval alone suffices.

### 8.2. Why admin review instead of fully automated?

- Supply-chain attack surface: automated signup lets an attacker
  claim names before legitimate publishers do.
- CVC is a small ecosystem; the review load is manageable.
- Admin review doubles as an introduction: new publishers get a human
  point of contact.
- We can relax to semi-automated (e.g. GitHub OAuth with org
  membership check) in a future minor release if the review queue
  becomes a bottleneck.

### 8.3. CLI registration helper

```bash
# Generate a keypair if the user doesn't have one:
cvcpkg identity init
# → writes ~/.config/cvcpkg/identity/ed25519.key (private)
# → writes ~/.config/cvcpkg/identity/ed25519.pub (public)

# Register with a server:
cvcpkg identity register --server https://pkg.cvc.example.org \
  --username alice --email alice@example.org
# → sends public key to server, prints "pending admin approval"

# Check status:
cvcpkg identity status --server https://pkg.cvc.example.org
```

---

## 9. Package name governance

### 9.1. Name reservation

The first time a publisher successfully publishes a package with a
given name, a `PackageName` row is created with `owner_id` pointing
to that publisher.  Subsequent publishes to the same name must come
from the same publisher (or an admin).

### 9.2. Admin controls

| Action | Endpoint | Who |
|--------|----------|-----|
| Block a name | `POST /v2/admin/names/{name}/block` | admin |
| Unblock a name | `POST /v2/admin/names/{name}/unblock` | admin |
| Transfer ownership | `POST /v2/admin/names/{name}/transfer` | admin |
| List all names | `GET /v2/admin/names` | admin |
| Revoke publisher | `POST /v2/admin/publishers/{id}/suspend` | admin |

### 9.3. Proactive name blocking

Before a release, admins can pre-reserve names for well-known
upstream projects (boost, hdf5, vtk, qt6, etc.) so that no one can
squat them.  The seed list is derived from `packaging/components.yaml`.

### 9.4. Name policy rules (enforced server-side)

- Names must match `^[a-z][a-z0-9_-]{0,63}$`.
- Names that are substrings or close Levenshtein-distance matches of
  a blocked name trigger a manual review flag (typo-squatting
  detection).
- The server logs every rejected publish attempt in the audit trail.

---

## 10. Cryptographic publisher identity

### 10.1. Key type

Ed25519 (via the `cryptography` package or `PyNaCl`).  Ed25519 is:

- Fast (sign + verify < 0.1 ms).
- Small keys (32 bytes public, 64 bytes private).
- Widely supported (OpenSSH, GPG, sigstore, TUF).
- No parameter choice risk (unlike RSA key sizes or EC curves).

### 10.2. Signing flow

```
Publisher (cvcpkg CLI)           cvcpkg-server
  │                                  │
  │  1. Build archive                │
  │  2. SHA-256 of archive bytes     │
  │  3. Ed25519 sign(sha256, privk)  │
  │                                  │
  │  POST /v2/publish                │
  │  { archive, signature, pubkey_fp}│
  │ ────────────────────────────────►│
  │                                  │  4. Look up publisher by fingerprint
  │                                  │  5. Verify signature against stored pubkey
  │                                  │  6. Verify SHA-256 matches uploaded bytes
  │                                  │  7. Check name ownership
  │                                  │  8. Store archive + signature
  │                                  │  9. Record in audit log
  │  ◄─ 201 { published }           │
```

### 10.3. Key rotation

- A publisher can register a **new** public key via
  `POST /v2/publishers/{id}/keys` (authenticated, requires current
  token).  The old key remains valid for verification of already-
  published artifacts but cannot sign new publishes after a
  configurable grace period (default: 30 days).
- Admins can force-rotate a publisher's key if compromise is
  suspected.

### 10.4. Client-side verification

On `cvcpkg install`, the client:

1. Fetches the package metadata (includes `signature` and
   `published_by` fingerprint).
2. Fetches the publisher's public key from the publisher directory.
3. Verifies the Ed25519 signature over the archive's SHA-256 digest.
4. If verification fails, the install is aborted with a clear error.
5. The `--trust-on-first-use` flag can be used to accept a publisher's
   key the first time and pin it for future installs (TOFU model,
   stored in `~/.config/cvcpkg/trusted_publishers.yaml`).

---

## 11. Publisher directory

### 11.1. Public endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /v2/publishers` | List all active publishers (paginated). |
| `GET /v2/publishers/{username}` | Publisher profile: display name, public key, fingerprint, packages owned. |
| `GET /v2/publishers/{username}/packages` | All packages published by this publisher. |
| `GET /v2/names/{name}` | Which publisher owns this package name + publishing history. |

### 11.2. Data exposed

- Username, display name (optional).
- Public key (PEM) and fingerprint.
- Account creation date.
- List of owned package names with latest version per platform.

What is **not** exposed:

- Email address (admin-only).
- Token details (never).
- Internal review notes (admin-only).

---

## 12. Admin revocation powers

### 12.1. Credential revocation

An admin can revoke a publisher's credentials at any time:

```
POST /v2/admin/publishers/{id}/suspend
{ "reason": "Compromised key — contacted publisher" }
```

This:
- Sets `Publisher.status = suspended`.
- Revokes all active tokens for that publisher.
- Marks all their packages as **yanked** (still downloadable for
  pinned lockfiles, but not resolved for new installs).
- Records the action in the audit log.

Re-enabling requires `POST /v2/admin/publishers/{id}/reinstate` +
a new public key upload by the publisher.

### 12.2. Name revocation

An admin can strip a publisher's ownership of a specific package name
without suspending their entire account:

```
POST /v2/admin/names/{name}/block
{ "reason": "Name squatting — reserving for upstream maintainer" }
```

This blocks new publishes to that name by anyone until the admin
explicitly transfers it or unblocks it.

### 12.3. Artifact revocation

Individual releases can be deleted (hard) or yanked (soft):

- **Yank**: `POST /v2/packages/{name}/{version}/yank` — existing
  lockfiles pointing to the exact SHA still resolve, but new
  `cvcpkg install` will not select the yanked version.
- **Delete**: `DELETE /v2/packages/{name}/{version}` — archive
  removed from all backends.  Lockfiles referencing it will fail.
  Admin-only.

---

## 13. Phased delivery plan

### Phase 0: prerequisite 1.x releases (v1.3.x – v1.4.x)

Work that can ship under the 1.x line without breaking changes:

- [ ] Formalize the filesystem catalog layout (`catalog.yaml` +
      `archives/` + optional `.sig` files).
- [ ] `cvcpkg push --dest <dir>` writes archives + regenerates
      `catalog.yaml` in the target directory.
- [ ] `cvcpkg install --catalog <dir>` resolves from a local
      filesystem catalog without any network access.
- [ ] Add S3 as a secondary CI publish target alongside GitHub
      Releases to begin the migration off GitHub storage.
- [ ] Harden the existing storage backend interface with retry logic
      and connection pooling.
- [ ] Add `cvcpkg identity init` and local Ed25519 keypair management
      (no server integration yet — just the key generation + storage).
- [ ] Add `signature` field to manifest schema (optional, ignored
      by 1.x server).
- [ ] Prototype SQLModel models alongside existing YAML store (dual-
      write for testing, YAML remains authoritative).
- [ ] Expand test coverage to ≥80%.

### Phase 1: database migration (v2.0.0-alpha)

- [ ] Replace YAML stores with SQLModel backend.
- [ ] `cvcpkg-server migrate-from-v1` CLI command.
- [ ] Alembic migration scaffolding for future schema changes.
- [ ] All existing 1.x server tests pass against SQLite backend.
- [ ] CI matrix: SQLite + PostgreSQL (via `docker-compose`).

### Phase 2: multi-backend router (v2.0.0-beta.1)

- [ ] `StorageBackend` table + admin CRUD endpoints.
- [ ] Backend router: ordered fallback on download, primary +
      async replication on publish.
- [ ] Health-check background task.
- [ ] Client-side fallback from lockfile URIs.

### Phase 3: publisher identity (v2.0.0-beta.2)

- [ ] `Publisher` table + registration flow endpoints.
- [ ] `cvcpkg identity init / register / status` CLI.
- [ ] Admin review endpoints (list pending, approve, reject,
      suspend, reinstate).
- [ ] Email field stored but not verified (admin review suffices).

### Phase 4: package name governance (v2.0.0-beta.3)

- [ ] `PackageName` table + ownership binding on first publish.
- [ ] Admin block/unblock/transfer endpoints.
- [ ] Typo-squatting detection (Levenshtein against blocked names).
- [ ] Seed blocked-name list from `packaging/components.yaml`.

### Phase 5: cryptographic signing (v2.0.0-rc.1)

- [ ] Ed25519 signature generation in `cvcpkg push`.
- [ ] Server-side signature verification on publish.
- [ ] Client-side signature verification on install.
- [ ] Key rotation endpoint + grace period.
- [ ] TOFU trust model with `trusted_publishers.yaml`.

### Phase 6: publisher directory (v2.0.0-rc.2)

- [ ] Public read-only endpoints for publisher profiles.
- [ ] `cvcpkg info --publisher <username>` CLI command.
- [ ] `cvcpkg info <package> --show-publisher` flag.
- [ ] HTML publisher directory page (optional, served by daemon).

### Phase 7: hardening & release (v2.0.0)

- [ ] Penetration testing against OWASP Top 10.
- [ ] Rate limiting on registration + publish endpoints.
- [ ] Comprehensive integration test suite (SQLite + PostgreSQL).
- [ ] Documentation: operator guide, publisher guide, security model.
- [ ] Performance benchmarking (target: 1000 packages, 50 publishers,
      4 backends).

### Phase 8: multi-platform expansion (v2.1.0+)

- [ ] Extend `platform.py` to detect FreeBSD, NetBSD, OpenBSD,
      HaikuOS, musl-Linux, and additional architectures (riscv64,
      ppc64le, s390x, mips64el, armv7l).
- [ ] Add `_common/env-freebsd.sh`, `env-netbsd.sh`, `env-haiku.sh`
      build environment scripts.
- [ ] Provision self-hosted runners for Tier B platforms (FreeBSD
      x86_64, Linux riscv64, Linux musl x86_64).
- [ ] Set up QEMU cross-build Docker images for riscv64, mips64el,
      ppc64le.
- [ ] Port core recipes (zlib, boost, fftw3, hdf5) to FreeBSD and
      Linux arm64/riscv64 as proof-of-concept.
- [ ] Add `cross-platform-actions/action` jobs for FreeBSD/NetBSD
      CI validation.
- [ ] Establish Tier A / B / C promotion criteria and tracking
      dashboard.
- [ ] Community contribution guide for adding new platform support.

---

## 14. New dependencies

| Package | Purpose | Version |
|---------|---------|---------|
| `sqlmodel` | ORM + Pydantic models | ≥0.0.22 |
| `alembic` | Database migrations | ≥1.13 |
| `asyncpg` | PostgreSQL async driver (optional) | ≥0.29 |
| `aiosqlite` | SQLite async driver | ≥0.20 |
| `aiomysql` | MySQL async driver (optional) | ≥0.2 |
| `cryptography` | Ed25519 key generation + signing | ≥43.0 |
| `python-jose` or `PyJWT` | JWT for optional OAuth flow | ≥3.3 |
| `httpx` | Async HTTP client for backend router | ≥0.27 |
| `celery` or `arq` | Async replication task queue (optional) | TBD |

All database drivers except `aiosqlite` are optional extras:
`pip install cvcpkg[server,postgres]` or `cvcpkg[server,mysql]`.

---

## 15. API versioning

The v2 server mounts all new endpoints under `/v2/`.  The existing
`/v1/` endpoints continue to work (backed by the same database) for
backward compatibility.  Deprecation timeline:

- v2.0: `/v1/` supported, no warnings.
- v2.1: `/v1/` emits `Deprecation` header.
- v3.0 (if ever): `/v1/` removed.

---

## 16. Deployment considerations

### 16.1. Single-node (lab / small team)

```bash
pip install cvcpkg[server]
cvcpkg-server run --database sqlite:///var/lib/cvcpkg/cvcpkg.db
```

SQLite mode — zero external dependencies.  Suitable for ≤5 publishers,
≤500 packages.

### 16.2. Production (multi-node)

```yaml
# docker-compose.yml sketch
services:
  db:
    image: postgres:17
    environment:
      POSTGRES_DB: cvcpkg
      POSTGRES_USER: cvcpkg
      POSTGRES_PASSWORD_FILE: /run/secrets/db_password
  server:
    image: ghcr.io/transfix/cvcpkg-server:2
    environment:
      DATABASE_URL: postgresql+asyncpg://cvcpkg:@db:5432/cvcpkg
      CVCPKG_STORAGE_PRIMARY: s3://cvc-packages/v2/
      CVCPKG_STORAGE_FALLBACK: https://github.com/transfix/libcvc-deps/releases/download/
    deploy:
      replicas: 3
```

### 16.3. Filesystem-only (no daemon, no network)

```bash
# Build and publish to a shared directory:
cvcpkg build-all --recipes-dir recipes --prefix /tmp/build \
  --platform linux --config release --link shared
cvcpkg pack-all --prefix /tmp/build --recipes-dir recipes --dist /tmp/dist
cvcpkg push /tmp/dist/*.tar.gz --dest /shared/cvcpkg-repo

# Consumers install from the directory:
cvcpkg install --catalog /shared/cvcpkg-repo --prefix ./deps boost hdf5
```

No daemon, no database, no network.  The shared directory can live
on NFS, a USB drive, or a local path.  This is the simplest
deployment and will always be supported.

### 16.4. Air-gapped / offline

An operator runs `cvcpkg mirror sync --from https://pkg.cvc.example.org
--to file:///mnt/packages` on an internet-connected machine, then
transfers the local directory to the air-gapped network.  The
air-gapped server serves from `file://` backend.  Alternatively,
the operator can skip the daemon entirely and point consumers at
the synced directory with `--catalog /mnt/packages`.

---

## 17. Security model summary

| Threat | Mitigation |
|--------|------------|
| Name squatting / typo-squatting | Admin review + Levenshtein detection + pre-reserved names |
| Compromised publisher key | Admin suspend → revoke all tokens + yank all packages |
| Tampered archive in transit | SHA-256 in catalog + Ed25519 signature verification |
| Tampered archive at rest | Backend stores are append-only from the server's perspective; integrity checked on read |
| Unauthorized publish | Token-scoped to publisher + name ownership check |
| Replay attack (re-publish old version) | UNIQUE constraint on (name, version, platform, arch, config, link) |
| Audit log tampering | Chained SHA-256 hashes (carried forward from 1.x) |
| Credential leakage | HMAC-hashed tokens; raw shown once at creation |
| Denial of service | Rate limiting on publish + register endpoints |
| Privilege escalation | Role hierarchy enforced server-side; admin role requires manual DB grant |

---

## 18. Open questions

1. **OAuth / OIDC integration**: Should we support "Log in with GitHub"
   for publisher registration?  This would let us auto-verify GitHub
   org membership (e.g. `transfix` org → auto-approve).  Adds
   complexity; defer to v2.1?

2. **Transparency log**: Should published artifacts be logged to a
   Sigstore-style transparency log?  Valuable for auditability but
   adds an external dependency.

3. **Artifact attestation**: Should we produce SLSA provenance
   attestations for CI-built packages?  The recipe-build workflow
   already runs in a known GitHub Actions environment.

4. **Quota management**: Per-publisher storage quotas?  Not needed
   for CVC-internal use but relevant if the registry opens to the
   broader scientific computing community.

5. **Webhook notifications**: Should the server emit webhooks on
   publish / yank events so CI systems can auto-update?

6. **Federation**: Should two cvcpkg-server instances be able to
   mirror each other's catalogs (peer-to-peer registry federation)?

7. **Cross-compilation toolchains**: Should cvcpkg ship or reference
   cross-compilation toolchain recipes (e.g. `riscv64-linux-gnu-gcc`)
   so that a single x86_64 host can build packages for all target
   architectures?  Or should we rely on QEMU user-mode and build
   natively inside emulated containers?

8. **BSD package manager integration**: On FreeBSD/NetBSD/OpenBSD,
   should recipes be allowed to declare `pkg` or `pkgsrc` system
   dependencies (analogous to `apt` on Linux, `brew` on macOS)?  Do
   we need a `_common/env-freebsd.sh` equivalent for each BSD?

9. **Runner cost model**: Self-hosted runners for exotic platforms
   (RISC-V boards, BSD VMs, Haiku) have ongoing maintenance and
   hosting costs.  Should these be community-funded, project-funded,
   or grant-funded?  Should community contributors be able to
   register their own build runners?
