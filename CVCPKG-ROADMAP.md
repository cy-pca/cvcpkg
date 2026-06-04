# cvcpkg Roadmap

> A cross-platform, language-agnostic binary package archive
> for the scientific computing community.

*Last updated: 2026-05-25*

---

## Vision

cvcpkg exists because existing package management systems have failed the
scientific computing community in one or more critical ways: they are bound to
a single language, locked to one operating system, overcomplicated, unreliable,
or unable to guarantee reproducible builds across platforms.

**cvcpkg is different.**  It is:

- **Language-agnostic** — packages are pre-built binaries (C, C++, Fortran,
  and eventually any compiled language).  There is no requirement to adopt a
  particular build system, runtime, or language ecosystem.
- **OS-agnostic** — first-class support for Linux, macOS, and Windows.
  Recipes declare platform-specific build logic; the archive serves every
  variant from a single namespace.
- **Simple but robust** — a single CLI (`cvcpkg`) handles building, publishing,
  installing, and verifying packages.  The server is a lightweight FastAPI
  application backed by PostgreSQL.
- **Reproducible** — each cvcpkg *release* pins a curated snapshot of recipes
  at known-good versions, giving downstream projects a stable foundation.
- **Community-extensible** — anyone can contribute recipes via pull request or
  publish packages to the archive through the authenticated API.

---

## Release Model

### Long-Term Support (LTS) Releases

Each **cvcpkg release** (e.g. v1.3.0, v2.0.0) is a curated, tested set of
recipes and the pre-built binary packages they produce.  A release is treated
as an LTS snapshot:

| Property | Description |
|---|---|
| **Recipe lockdown** | All recipes in the release are pinned to specific versions with verified SHA-256 checksums. |
| **Cross-platform matrix** | Every recipe is built and tested on Linux (x86_64), macOS (x86_64, arm64), and Windows (x64). |
| **Stability guarantee** | Packages in a release are immutable — once published, they do not change.  Broken packages are *yanked*, not updated in place. |
| **Downstream reproducibility** | Projects depending on a cvcpkg release can rebuild at any time and get identical binary artifacts. |
| **Security patching** | Critical CVEs can trigger a point release (e.g. v1.3.1) that replaces only the affected package while keeping everything else identical. |

### Live Recipes

Between releases, **updated and new recipes are available live** in the
repository and on the archive server.  This allows:

- Recipe authors to iterate on build scripts before a release freeze.
- Community contributors to submit new recipes that are not yet part of an
  official release.
- Downstream users to opt in to bleeding-edge packages by explicitly
  requesting them (e.g. `cvcpkg install boost@live`).

When the next release is being hardened, live recipes that meet quality
standards are promoted into the release manifest.

### Release Workflow

```
┌─────────────┐     ┌──────────────┐     ┌────────────────┐
│  Develop     │────▶│  Candidate   │────▶│  Release       │
│  (live)      │     │  (freeze)    │     │  (LTS tag)     │
└─────────────┘     └──────────────┘     └────────────────┘
     ▲                                         │
     │          bug fixes / CVE patches        │
     └─────────────────────────────────────────┘
```

1. **Development phase** — recipes land on `main`, are built by CI, and
   published to the archive as live packages.
2. **Candidate phase** — a release branch is cut, only bug fixes are merged,
   full cross-platform CI matrix runs, community testing is solicited.
3. **Release** — the branch is tagged, all packages are signed, the release
   manifest is published.  The `prod` branch is updated and the production
   server deploys automatically.

---

## Architecture

### Current (v0.1.0)

```
┌────────────────────────────────────────────────────┐
│                    cvcpkg.org                      │
│   Apache2 + Let's Encrypt (TLS termination)        │
│        │                                           │
│        ▼                                           │
│   FastAPI (cvcpkg-server)          port 8420       │
│        │                                           │
│        ├── /            Landing page (package index)│
│        ├── /v1/catalog  Public catalog JSON         │
│        ├── /v1/packages Package listing + search    │
│        ├── /v1/download Package binary download     │
│        ├── /v1/publish  Authenticated publish       │
│        ├── /v1/tokens   Token management (admin)    │
│        └── /v1/audit    Tamper-evident audit trail   │
│        │                                           │
│        ▼                                           │
│   PostgreSQL 16                                    │
│   (packages, tokens, audit log)                    │
└────────────────────────────────────────────────────┘
```

### Deployment

- **Host:** catx-03.tx.wtf (Ubuntu 22.04, x86_64)
- **Containerization:** Docker Compose (postgres + backend)
- **CI/CD:** GitHub Actions → `prod` branch push → self-hosted runner →
  auto-deploy script → zero-downtime restart
- **TLS:** Let's Encrypt via certbot, auto-renewal

---

## Roadmap Phases

### Phase 1 — Foundation (Current)

**Status: In Progress**

- [x] Recipe-based build system with 30 component recipes
- [x] `cvcpkg` CLI (build, install, publish, verify)
- [x] FastAPI server with YAML + PostgreSQL dual backend
- [x] HMAC-SHA256 token authentication with role-based access
- [x] Chained-hash tamper-evident audit trail
- [x] Ed25519 package signing
- [x] Docker production deployment
- [x] Apache2/Let's Encrypt TLS on cvcpkg.org
- [x] Landing page with package index, search, and sorting
- [x] CI/CD deploy pipeline (prod branch → auto-deploy)
- [ ] Self-hosted GitHub runner on catx-03
- [ ] Cross-platform CI build matrix (Linux, macOS, Windows)
- [ ] Initial set of published packages on cvcpkg.org

### Phase 2 — Analytics & Telemetry

**Status: Planned**

Package administrators need visibility into how the archive is being used
to make informed decisions about resource allocation, deprecation, and
support priorities.

#### Download Analytics

- **Per-package download counts** — total, last 7/30/90 days, all time.
- **Per-version breakdown** — which versions are actively consumed vs. stale.
- **Platform distribution** — what fraction of downloads are Linux vs. macOS
  vs. Windows.  This drives build priority decisions.
- **Geographic distribution** — coarse geo-IP bucketing (continent/country)
  for CDN planning.
- **Temporal patterns** — download trends over time, spike detection.

#### Bandwidth Accounting

- **Per-package bandwidth** — total bytes served per package, per version.
- **Aggregate bandwidth** — daily/weekly/monthly totals for capacity planning.
- **Top consumers** — identify heavy-usage patterns (CI farm vs. individual
  developer downloads).

#### Client Telemetry (Opt-In)

- **Client version distribution** — track cvcpkg CLI versions in the wild to
  inform deprecation timelines.
- **Build environment fingerprints** — OS, architecture, compiler, CMake
  version — anonymized and aggregated.  Helps recipe authors understand the
  environments their packages actually run on.
- **Install success/failure rates** — per-package, per-platform.  Surfaces
  broken recipes quickly.
- **Resolution time** — how long dependency resolution takes as the catalog
  grows.

#### Implementation Plan

1. Add a `download_events` table: `(id, package_id, timestamp, client_ip_hash,
   user_agent, platform, arch, cvcpkg_version, bytes_sent)`.
2. Record download events in the `/v1/download` endpoint asynchronously
   (non-blocking to the download itself).
3. Add admin API endpoints:
   - `GET /v1/analytics/downloads` — filterable download counts
   - `GET /v1/analytics/bandwidth` — bandwidth usage summaries
   - `GET /v1/analytics/platforms` — platform distribution
   - `GET /v1/analytics/trends` — time-series data
4. Display analytics on the landing page and admin dashboard.
5. Client-side telemetry: add an opt-in `cvcpkg telemetry` command that sends
   anonymized build environment data on install.  Controlled by
   `CVCPKG_TELEMETRY=1` env var.  Off by default — never phone home without
   explicit consent.

#### Privacy

- IP addresses are salted-hashed, never stored in plain text.
- Client telemetry is strictly opt-in.
- No personal data is collected — only technical environment metadata.
- All analytics are aggregate; no individual user tracking.

### Phase 3 — Admin Dashboard

**Status: Planned**

A web-based administration interface at `/admin` for managing the cvcpkg
archive without CLI access.

#### Features

- **Package management** — browse, search, yank, unyank, delete packages.
  Inspect signatures, checksums, download counts.
- **Token management** — create, revoke, list API tokens.  View token
  usage history.
- **Audit trail viewer** — search and filter the tamper-evident audit log.
  Verify chain integrity from the UI.
- **Analytics dashboard** — real-time charts: download trends, platform
  distribution, bandwidth usage, top packages, geographic distribution.
- **Release management** — create and manage LTS releases.  Promote live
  recipes into release manifests.  Trigger cross-platform CI builds.
- **Health monitoring** — server uptime, database stats, disk usage,
  certificate expiry.
- **User/org management** (future) — manage publisher accounts, organization
  namespaces, permission scopes.

#### Technical Approach

- Server-side rendered HTML with lightweight JS (htmx or Alpine.js) — no
  heavy SPA framework.  Keeps the deployment simple.
- Admin routes protected by admin-role token auth (cookie-based session after
  initial token login).
- Built into the existing FastAPI application as a sub-router — no separate
  service to deploy.

### Phase 4 — Multi-Language & Ecosystem Expansion

**Status: Future**

cvcpkg currently focuses on C/C++ libraries, but the architecture is
deliberately language-agnostic.  Future expansion:

#### Language Support

- **Fortran** — scientific computing staple, many legacy codebases.
- **Rust** — pre-built native libraries with C-compatible ABI.
- **Python C extensions** — pre-compiled wheels for scientific packages that
  are notoriously hard to build (BLAS, LAPACK, HDF5 bindings).
- **Julia artifacts** — native library packages for Julia's Pkg system.

#### Recipe Ecosystem

- **Recipe templates** — `cvcpkg init` generates a recipe from a template
  (CMake, Meson, Autotools, custom).
- **Recipe validation** — `cvcpkg lint` checks recipes for common errors,
  missing fields, insecure patterns.
- **Dependency resolution** — recipes declare build-time and runtime
  dependencies on other cvcpkg packages.  The resolver computes a dependency
  graph and builds packages in the correct order.
- **Build caching** — cache intermediate build artifacts (object files, CMake
  configs) to speed up CI builds.

#### Interoperability

- **CMake `find_package` integration** — `cvcpkg install` generates
  `<Package>Config.cmake` files so downstream CMake projects can
  `find_package(Boost)` transparently.
- **pkg-config support** — generate `.pc` files for each installed package.
- **Spack compatibility layer** — import/export recipes from Spack specs.
- **Conan compatibility layer** — consume Conan recipes as cvcpkg recipes.
- **vcpkg manifest mode** — read `vcpkg.json` and resolve packages from the
  cvcpkg archive.

### Phase 5 — Federation & Scaling

**Status: Future**

As the archive grows, a single server won't suffice.  Plan for horizontal
scaling and federation:

- **CDN integration** — serve package archives from CloudFlare R2, AWS S3,
  or similar.  The server becomes a metadata/API layer; binaries are served
  from edge locations.
- **Mirror protocol** — institutions can run local mirrors that sync from
  the primary server.  Useful for air-gapped environments and reducing
  bandwidth costs.
- **Federated registries** — multiple independent cvcpkg servers can
  cross-reference packages.  A client can query multiple registries
  with fallback.
- **Sharded storage** — split the archive across multiple storage backends
  by package name hash or category.
- **Read replicas** — PostgreSQL streaming replication for read-heavy
  workloads.

### Phase 6 — Community & Governance

**Status: Future**

- **Organization namespaces** — `@org/package` scoping for institutional
  publishers.
- **Package ownership model** — maintainers, co-maintainers, transfer
  process.
- **Review workflow** — community recipe PRs go through automated CI +
  manual review before being merged.
- **Quality tiers** — packages rated by test coverage, platform coverage,
  maintainer responsiveness.
- **Advisory database** — track CVEs affecting published packages, automatic
  yank + advisory notification for affected versions.
- **Documentation hosting** — render recipe README.md on the package page.

---

## Package Recipes

### Current Recipes (v1.3.0)

| Recipe | Category | Description |
|---|---|---|
| abseil | Core | Google's C++ common libraries |
| boost | Core | Boost C++ libraries |
| c-ares | Network | Async DNS resolver |
| cgal | Geometry | Computational Geometry Algorithms Library |
| clapack | Math | LAPACK in C |
| fftw3 | Math | Fast Fourier Transform |
| grpc | Network | Google RPC framework |
| gsl | Math | GNU Scientific Library |
| hdf5 | Data | Hierarchical Data Format |
| imagemagick | Imaging | Image manipulation toolkit |
| lerc | Data | Limited Error Raster Compression |
| levmar | Math | Levenberg-Marquardt optimization |
| libiimod | Imaging | IMOD image library |
| libjpeg-turbo | Imaging | JPEG codec |
| libwebp | Imaging | WebP codec |
| log4cplus | Core | Logging framework |
| nfft3 | Math | Non-equispaced FFT |
| openblas | Math | Optimized BLAS |
| openssl | Security | TLS/crypto toolkit |
| protobuf | Data | Protocol Buffers |
| pthreads4w | Core | POSIX threads for Windows |
| qt6 | GUI | Qt 6 framework |
| re2 | Core | Regular expression engine |
| tiff | Imaging | TIFF codec |
| vtk | Visualization | Visualization Toolkit |
| xz | Compression | XZ/LZMA compression |
| yaml | Data | YAML parser |
| zlib | Compression | Deflate compression |
| zstd | Compression | Zstandard compression |

### Recipe Categories

Recipes are organized into functional categories to make the archive
browsable and to help users discover related packages:

- **Core** — fundamental libraries (Boost, abseil, logging, threading)
- **Math** — numerical computing (BLAS, LAPACK, FFT, optimization)
- **Imaging** — image codecs and manipulation (JPEG, TIFF, WebP, ImageMagick)
- **Data** — serialization and data formats (HDF5, Protocol Buffers, YAML)
- **Compression** — archive and compression (zlib, zstd, xz)
- **Network** — networking and RPC (gRPC, c-ares, OpenSSL)
- **Geometry** — computational geometry (CGAL)
- **Visualization** — 3D rendering and visualization (VTK)
- **GUI** — graphical user interface frameworks (Qt6)
- **Security** — cryptography and TLS (OpenSSL)

---

## Design Principles

1. **Simplicity over cleverness.**  A graduate student should be able to
   understand the system in an afternoon.  One CLI, one server, one database.

2. **Reproducibility is non-negotiable.**  If a build worked yesterday, it
   must work next year.  Pinned versions, checksums, signed packages.

3. **Cross-platform is a first-class citizen.**  Not an afterthought.  Every
   recipe must build on Linux, macOS, and Windows or clearly document
   platform restrictions.

4. **No vendor lock-in.**  The archive runs on commodity hardware with open
   source software.  The protocol is HTTP + JSON.  Any client can
   interoperate.

5. **Security by default.**  TLS everywhere, signed packages, tamper-evident
   audit trail, role-based access control.  But also: no unnecessary
   complexity.  HMAC-SHA256 tokens are simpler than OAuth and sufficient for
   the threat model.

6. **Community-first.**  The system should be easy to contribute to.  Recipe
   format is YAML — no DSL to learn.  Publishing is a single CLI command.

7. **Data-driven decisions.**  Analytics and telemetry (always opt-in) help
   administrators prioritize effort where it matters most.

---

## Naming

The project is currently named **libcvc-deps** for historical reasons.  It
will be renamed to **cvcpkg** in a future release.  This rename will:

- Update the GitHub repository name
- Update all CLI entry points
- Update package metadata and documentation
- Maintain backward compatibility for existing URLs and configurations
  through redirects

The rename is deferred to avoid breaking existing CI pipelines and
downstream references during the active stabilization period.

---

## Contributing

See the [GitHub repository](https://github.com/transfix/libcvc-deps) for:

- Recipe authoring guide
- Server development setup
- CI/CD pipeline documentation
- Pull request process

---

*This is a living document.  It will be updated as the project evolves.*
