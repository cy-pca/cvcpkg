# cvcpkg Roadmap

> A cross-platform, language-agnostic binary package archive
> for the scientific computing community.

*Last updated: 2026-07-10*

---

## Project Rename — `libcvc-deps` → `cvcpkg`

The project is being renamed to **`cvcpkg`** in its entirety; the
`libcvc-deps` name is being dropped.  `cvcpkg` — the CLI, the server, the
recipe archive, and the PyPI distribution — becomes the single name for the
project going forward.

- The Python distribution is already published as **`cvcpkg`** (not
  `libcvc-deps`).
- The GitHub repository will be renamed **`transfix/libcvc-deps` →
  `transfix/cvcpkg`** *before* the first PyPI publish (see Phase 1.5).
- Backward compatibility is retained where downstream consumers depend on
  the old name — e.g. the `libcvc-depsConfig.cmake` compatibility wrapper
  stays so existing `find_package(libcvc-deps)` calls keep working.

> **Release ordering:** the PyPI publish is the **final** step of the
> release — it happens only after the rename (project + repo), the trusted
> publisher is configured for the new repo, and the remaining roadmap gaps
> are closed.

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

- **Primary host:** cvcpkg.org (cvcpkg-00, 10.10.10.134)
- **Mirror host:** pkg.tx.wtf (catx-03, local read-only mirror)
- **Containerization:** Docker Compose (postgres + backend)
- **CI/CD:** GitHub Actions → `prod` branch push → self-hosted runner →
  auto-deploy script → zero-downtime restart
- **TLS:** Let's Encrypt via certbot, auto-renewal
- **Builders:** 13 builder agents across 7 platforms:
  - Linux x86_64: star-00, star-01, lat, rebota (self-hosted runners)
  - FreeBSD x86_64: freebsd-build, freebsd-build-2 (Incus containers)
  - NetBSD x86_64: netbsd-build, netbsd-build-2 (Incus containers)
  - OpenBSD x86_64: openbsd-build, openbsd-build-2 (Incus containers)
  - Windows x86_64: sandipaws, stablefarm-win11 (self-hosted)
  - macOS (x86_64 + arm64): GitHub-hosted runners via workflow_dispatch

---

## Roadmap Phases

### Phase 1 — Foundation

**Status: Complete (v2.0.0)**

- [x] Recipe-based build system with 99 component recipes
- [x] `cvcpkg` CLI (build, install, publish, verify, validate)
- [x] FastAPI server with PostgreSQL backend + Alembic migrations
- [x] HMAC-SHA256 token authentication with role-based access
- [x] Chained-hash tamper-evident audit trail
- [x] Ed25519 package signing
- [x] Docker production deployment (cvcpkg.org + pkg.tx.wtf mirror)
- [x] Apache2/Let's Encrypt TLS on cvcpkg.org
- [x] Landing page with package index, search, sorting, and org scoping
- [x] CI/CD deploy pipeline (prod branch → auto-deploy to both hosts)
- [x] Self-hosted GitHub runners (catx-03, star-00, star-01, lat, rebota)
- [x] 13 builder agents across Linux, FreeBSD, NetBSD, OpenBSD, Windows, macOS
- [x] Cross-platform CI build matrix (Linux, macOS, Windows, 3 BSDs)
- [x] 650+ published packages on cvcpkg.org
- [x] Organization namespaces with member management
- [x] Remote builder orchestration (submit-dag, follow-dag, monitor)
- [x] `cvcpkg install --from cvc-requirements.yaml` workflow
- [x] Activation scripts (bash, zsh, fish, PowerShell) setting CMAKE_PREFIX_PATH
- [x] CMake integration (cvcpkgConfig.cmake, libcvc-deps compat, toolchain file)
- [x] cvcpkg promoted to repo root (pyproject.toml at root)
- [x] Cosmopolitan (cosmo) cross-compilation platform support
- [x] WASM/WASI build platform support
- [x] GTK4 dependency stack (pixman → cairo → pango → gdk-pixbuf → gtk4)
- [x] Recipe validation (JSON schema, dependency graph, CI checks)
- [x] Source-fallback builds (build from source when no binary available)
- [x] Build caching (server-cache for CI and builders)
- [x] Mirror protocol (pkg.tx.wtf mirrors cvcpkg.org with sync loop)

### Phase 1.5 — PyPI Release Readiness

**Status: In Progress**

Items required before `pip install cvcpkg` goes live on PyPI.

#### Packaging & Distribution

Ordered — the PyPI publish is the **last** step and happens only after the
rename and the remaining gaps are closed.

- [x] pyproject.toml at repo root with poetry-core backend
- [x] `cvcpkg` and `cvcpkg-server` entry points
- [x] Recipes bundled into wheel via CI publish workflow
      (fixed: the publish workflow's bundle step now creates the target
      dir, fails loudly, and verifies the built wheel contains recipes)
- [x] Build + live-smoke the wheel on Linux/macOS/Windows via a release
      candidate tag (`cvcpkg-v2.0.0rc6`: 129 recipes bundled, all green)
- [x] Verify `cvcpkg --version` and `cvcpkg-server --version` from the
      installed wheel
- [ ] **Rename the project to `cvcpkg`, dropping `libcvc-deps`** (see the
      Project Rename section above)
- [ ] **Rename the GitHub repo `transfix/libcvc-deps` → `transfix/cvcpkg`**
- [ ] **Configure the PyPI trusted publisher** for the renamed repo:
      owner `transfix`, repo `cvcpkg`, workflow `cvcpkg-publish.yml`,
      environment `pypi`.  (The earlier stable publish failed with
      `invalid-publisher` because no matching trusted publisher exists.)
- [ ] Close the remaining roadmap gaps below
- [ ] **Publish v2.0.0 to PyPI — final release step.**  Gated behind the
      `CVCPKG_PUBLISH_TO_PYPI` repo variable so a stable tag does not
      publish until the rename + trusted publisher are in place.

#### Admin CLI Completeness

- [x] `cvcpkg token` — create, list, revoke, approve/deny requests
- [x] `cvcpkg builder` — list, status, run, unregister
- [x] `cvcpkg builds` — submit, submit-dag, list, cancel, pause, resume, follow-dag, monitor
- [x] `cvcpkg server` — stop, status
- [x] `cvcpkg org` — members, add-member, remove-member
- [x] `cvcpkg recipe` — push (sync recipes to server DB)
- [x] `cvcpkg server stats` — server resource + catalog statistics (DB backend, package/org/builder/job/audit counts, storage)
- [x] `cvcpkg server backup` — trigger a server-side database backup from CLI (sqlite/pg_dump/mysqldump)
- [x] `cvcpkg builder logs` — view recent build jobs / tail a job's log without the full monitor
- [x] `cvcpkg doctor` — diagnose the local toolchain (Python, CMake, Ninja, compiler, git) and server reachability

#### Testing & Quality

- [x] 927 unit tests passing
- [x] 16 integration test modules (server, browser UI, build cache, e2e lifecycle, etc.)
- [x] Integration tests for cvc-requirements.yaml install workflow
- [x] Integration tests for dummy recipe build lifecycle
- [x] CMake integration tests (configure, install, downstream find_package)
- [x] E2E live test (Docker Compose + real server + builder + compile consumer)
- [x] Recipe validation via JSON schema in CI
- [x] Source-fallback integration tests (Linux, macOS)
- [ ] Windows CI integration tests
- [x] Automated upgrade/migration test (v1.x → v2.0.0)
      (migration-chain integrity + legacy-lockfile read; live PostgreSQL migration runs in the Docker job)
- [x] Performance benchmarks for install/resolve with large catalogs (resolver regression guard on a 400-component chain)

#### CMake Integration

- [x] `cvcpkgConfig.cmake` — sets CMAKE_PREFIX_PATH + PKG_CONFIG_PATH
- [x] `libcvc-depsConfig.cmake` — backward-compat wrapper
- [x] `cvcpkg-toolchain.cmake` — for `-DCMAKE_TOOLCHAIN_FILE=` usage
- [x] Activation scripts set CMAKE_PREFIX_PATH on `source activate`
- [x] Document CMake integration in README and docs/ (docs/cmake-integration.md)
- [x] `cvcpkg install` writes cvcpkgConfig.cmake into the prefix automatically
      (also a libcvc-deps compat config + version files)

#### Documentation

- [x] README.md (quick start, install, build, publish)
- [x] docs/api-reference.md
- [x] docs/ci-cd-pipeline.md
- [x] docs/deployment-guide.md
- [x] docs/organizations.md
- [x] docs/cmake-integration.md — CMake usage guide for downstream projects
- [x] docs/recipe-authoring.md — how to create new recipes
- [x] docs/pypi-install.md — pip install guide and extras reference
- [x] CHANGELOG.md — release notes for v2.0.0

#### Gap Analysis

The following items were identified as potential gaps before the PyPI
release.  These are the gaps to close **before** the final publish step.

1. ~~**No CHANGELOG.md**~~ — ✅ `CHANGELOG.md` exists; still needs a
   v2.0.0 entry covering the `cvcpkg` tool changes (doctor, admin CLI,
   recipe-bundling fix, NullPool fix, postgresql recipes) and the rename.
2. ~~**No `cvcpkg doctor` command**~~ — ✅ Done.  `cvcpkg doctor` checks
   Python, pip, CMake, Ninja, a C/C++ compiler, git, and (optionally)
   server reachability.
3. ~~**No `cvcpkg init` command**~~ — ✅ `cvcpkg init <name>` scaffolds a
   schema-valid recipe (recipe.yaml + build scripts) for cmake, meson, or
   autotools.
4. ~~**No `cvcpkg upgrade` command**~~ — ✅ `cvcpkg upgrade [components]`
   checks the catalog for newer versions of the installed components and
   re-installs just those, updating the lockfile (`--dry-run` previews).
5. ~~**No offline mode documentation**~~ — ✅ documented in
   docs/pypi-install.md (local catalog + `--local` source builds + cache).
6. **Recipe test coverage** — not all recipes have been built and
   tested on all 7 platforms.  GTK4 stack is being built now.
7. ~~**Signature verification not enforced**~~ — ✅ `cvcpkg install
   --require-signatures` now fails on any unsigned or invalidly-signed
   package; `--verify-signatures` verifies when a signature is present.
   (Making enforcement the default remains a future policy decision.)
8. ~~**No dependency version constraints**~~ — ✅ dependencies carry
   version ranges (e.g. `^3.0`, `==1.3.0`) in the recipe/catalog, and the
   resolver enforces them — user + transitive constraints, with
   intersection and conflict rejection (see test_resolver.py).

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
  `cvcpkgConfig.cmake` so downstream CMake projects can
  `find_package(cvcpkg)` and then `find_package(Boost)` transparently.
  *(Partially done — config files exist, auto-install into prefix pending.)*
- **pkg-config support** — generate `.pc` files for each installed package.
  *(Partially done — toolchain sets PKG_CONFIG_PATH, recipes generate .pc files.)*
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

**Status: Partially Done**

- [x] **Organization namespaces** — `@org/package` scoping for institutional
  publishers, with member management CLI and API.
- [ ] **Package ownership model** — maintainers, co-maintainers, transfer
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

### Current Recipes (v2.0.0) — 99 recipes

| Category | Recipes |
|---|---|
| **Core** | abseil, boost, log4cplus, pthreads4w, re2, readline |
| **Math** | clapack, fftw3, gsl, levmar, mpfr, nfft3, openblas |
| **Imaging** | imagemagick, lerc, libjpeg-turbo, libpng, libwebp, tiff |
| **Data** | hdf5, protobuf, yaml |
| **Compression** | bzip2, lz4, xz, zlib, zstd |
| **Network** | c-ares, curl, grpc, libpq, miniupnpc, openssl |
| **Geometry** | cgal, vcglib |
| **Visualization** | vtk |
| **GUI / Graphics** | cairo, fontconfig, freetype, fribidi, gdk-pixbuf, graphene, gtk4, harfbuzz, libepoxy, pango, pixman, qt6, qtmultimedia, qtshadertools, skia, slint, wayland, wayland-protocols, xkbcommon |
| **Audio** | ffmpeg, gstreamer, libpulse, libsndfile, pipewire |
| **Build tools** | autoconf, automake, bazel, bison, cmake, cosmocc, emsdk, flex, libtool, m4, meson, nasm, ninja, swig |
| **Python** | python311, python312, python313 |
| **Databases** | mariadb-connector-c, sqlite |
| **Runtime / Interop** | libffi, libunistring, iconv, idn2, pcre2, wamr, wasi-sdk, wasmedge, wasmer, wasmtime |
| **Security** | ca-bundle |
| **Text** | aspell, gettext, glib, lua |
| **Misc** | f2c, gmp, libiimod |

### Recipe Categories

Recipes are organized into functional categories to make the archive
browsable and to help users discover related packages:

- **Core** — fundamental libraries (Boost, abseil, logging, threading)
- **Math** — numerical computing (BLAS, LAPACK, FFT, optimization)
- **Imaging** — image codecs and manipulation (JPEG, PNG, TIFF, WebP, ImageMagick)
- **Data** — serialization and data formats (HDF5, Protocol Buffers, YAML)
- **Compression** — archive and compression (zlib, zstd, xz, lz4, bzip2)
- **Network** — networking and RPC (gRPC, c-ares, OpenSSL, curl)
- **Geometry** — computational geometry (CGAL, vcglib)
- **Visualization** — 3D rendering and visualization (VTK)
- **GUI / Graphics** — graphical user interface frameworks (Qt6, GTK4, Slint, Skia)
- **Audio** — audio/video processing (FFmpeg, GStreamer, PipeWire)
- **Build tools** — compilers and build systems (CMake, Meson, Ninja, Autotools)
- **Python** — Python interpreters (3.11, 3.12, 3.13)
- **Databases** — database clients and embedded databases (MariaDB, SQLite)
- **Runtime / Interop** — FFI, WASM runtimes, text/encoding utilities
- **Security** — CA certificates, TLS

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

## Naming & Repo Identity

The project has been restructured for the **cvcpkg** identity:

- [x] `cvcpkg` CLI entry point and Python package name
- [x] pyproject.toml at repo root (promoted from tools/cvcpkg/)
- [x] `cvcpkgConfig.cmake` installed alongside backward-compat `libcvc-depsConfig.cmake`
- [x] README.md and docs reflect cvcpkg branding
- [ ] **Repo rename** — `transfix/libcvc-deps` → `transfix/cvcpkg` (deferred
  until PyPI release to avoid breaking CI in downstream repos; GitHub will
  redirect git URLs but `uses:` directives in libcvc and TexMol workflows
  must be updated simultaneously)
- [ ] **PyPI publication** — `pip install cvcpkg` (pending final QA)

---

## Contributing

See the [GitHub repository](https://github.com/transfix/libcvc-deps) for:

- Recipe authoring guide (see `recipes/zlib/` as a reference template)
- Server development setup (`pip install -e ".[production]"`)
- CI/CD pipeline documentation (`docs/ci-cd-pipeline.md`)
- Pull request process (squash-merge via admin review)

Quick start for recipe contributors:

```bash
pip install -e .
cvcpkg validate                     # validate all recipes
cvcpkg build zlib --prefix ./prefix # build a single recipe
cvcpkg pack zlib --output-dir dist  # build + archive
```

---

*This is a living document.  It will be updated as the project evolves.*
