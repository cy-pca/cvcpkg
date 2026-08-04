# Changelog

All notable changes to the **libcvc-deps** prebuilt bundles are
recorded here. Each tagged release of libcvc-deps records the
**exact upstream versions actually shipped** in the release
artifacts. Consumers who need bit-for-bit reproducible bundles
should consume a tagged release rather than the moving tip.

A given release of libcvc-deps is intended to be used with the
corresponding (or older) libcvc release. Bumping a major version
of Qt or VTK is reflected by bumping libcvc-deps's own version.

Some components are pinned in the workflow itself (VTK, Qt on
Windows, log4cplus, NFFT, vcglib, libiimod, levmar, the Windows
ImageMagick overlay port). Others are taken as-is from each
platform's package manager and therefore drift between releases.

The format follows [Keep a Changelog](https://keepachangelog.com/)
and the project adheres to [Semantic Versioning](https://semver.org/).

Starting with v1.3.0 the focus shifts from monolithic per-platform
bundles to **per-component archives served by `cvcpkg`** (the
`cvcpkg.org` registry).  The "Full component manifest" tables in the
older v1.0.x / v1.1.0 entries describe the legacy monolithic-zip
shape; from v1.3.0 onward consumers should consume individual
component bundles through the `cvcpkg` CLI, with upstream pins
documented per-recipe in `recipes/<name>/recipe.yaml`.

---

## v2.0.0

Major release: production daemon with database backend, the
`cvcpkg.org` registry, distributed build infrastructure, and wasi
support.

### Packaging: the client installs on click + PyYAML alone (2026-08-03)

`pip install cvcpkg` now pulls **two** third-party packages, `click` and
`PyYAML`, and nothing else. `sqlalchemy`, `cryptography`, `httpx` and
`greenlet` were declared mandatory but are never imported by the client:
HTTP on the install path is `urllib.request` (`cvcpkg.storage`), integrity
is `hashlib.sha256`, signing is opt-in and lazily imported, and the ORM is
server-side. The four are now extras, named after the **entry point** that
needs them rather than the wheel they pull — `remote` (any command that
talks to a registry: search, `recipe pull`, `builds list/log/monitor`,
webhooks, token/user/org admin, `doctor --server`), `publish` (`publish`,
`recipe push`, `builds submit-dag`), `builder` (the agent), `signing`
(`key`/`sign`/`verify-sig`, `install --verify-signatures`), and `server`
(the ASGI stack plus SQLAlchemy and greenlet). The first three resolve to
the same `httpx`; the separate names exist so the error a user gets names
their own command's extra.

Reaching a command whose extra is missing is a single line — `httpx is
required to talk to a cvcpkg server over HTTP. Install it with: pip install
'cvcpkg[publish]'` — not a `ModuleNotFoundError` traceback; the guards live
in the new `cvcpkg.optional` and follow the shape `cvcpkg-server run` has
always used for a missing uvicorn. That covers the server's own
state-directory commands too — `cvcpkg-server bootstrap`, `token create`,
`audit log`/`verify` reach `cvcpkg.server.models` for its enums without ever
opening a database, so `pydantic` is guarded there alongside the SQLAlchemy
guards in `db.py`/`db_stores.py`. Two callers degrade instead: `cvcpkg
doctor --server` reports it as one failed check rather than aborting the
whole report, and `cvcpkg build`'s optional pull of the server recipe set
says so and falls back to the local recipes, since fetching them is an
optimization and not the command's job. `cvcpkg/__init__.py` also gained
a `PackageNotFoundError` fallback for `__version__`, so a source checkout on
`PYTHONPATH` imports at all — the pip-free install route.

`jsonschema` joins them as the `validate` extra. It was never a declared
runtime dependency at all — only a dev dependency, hand-installed by each
workflow that validates — so `cvcpkg validate` on any other install died with
a bare `ModuleNotFoundError: No module named 'jsonschema'` at
`validation.py`, the last such traceback on a core install. That is the
command `cvcpkg init` ends by telling you to run, and the one `cvcpkg --help`
advertises in its recipe-maintainer quick start; it now says `jsonschema is
required to validate recipes against their schema. Install it with: pip
install 'cvcpkg[validate]'`, and `init`'s step 3 names the extra up front
when it is missing. Recipe *installation* is untouched — nothing on the
catalog, resolve, download or lockfile path reads a schema — so the core stays
`click` + `PyYAML`.

**What this changes for existing users:** nothing installs itself that you
did not ask for any more, so an environment that publishes, runs a builder,
signs, or hosts the server must name its extra —
`pip install 'cvcpkg[publish]' / '[builder]' / '[signing]' / '[server,db]'`.
`pip install 'cvcpkg[all]'` is the one-word migration: a superset of what
the old mandatory list gave you.

**Every in-repo workflow that installs cvcpkg now names the extra its own
commands need**, because a bare `pip install .` on a host that then makes an
HTTP call is exactly the regression this split invites. Builder hosts take
`[builder]` (`deploy-dev.yml`, `deploy-prod.yml`); the jobs that run `recipe
push`/`push-all`, `builds submit-dag`/`follow-dag` or `publish` take
`[publish]` (`deploy-prod.yml`, `populate-server.yml` — the workflow that
populates the public catalog — `populate-dev.yml`, `pr-recipe-build-dev.yml`,
`windows-build.yml`); registry admin (`yank`/`unyank`/`nuke`) takes `[remote]`
(`package-lifecycle.yml`); `ci.yml` installs `[all]` so the unit suite's
httpx/cryptography/sqlalchemy tests actually run instead of silently skipping.
The PyInstaller binaries take `[remote,signing,validate]`
(`[production,signing,validate]` for the combined build) — those bundle a
fixed closure, so the now-guarded imports have to be present at build time and
are named explicitly in the spec. Jobs that only validate recipes or the
dependency graph take `[validate]` and nothing else (`cvcpkg-ci.yml`),
replacing the ad-hoc `pip install jsonschema` they each carried.
`docs/pypi-install.md` is the extras reference;
`docs/ci-cd-pipeline.md`, `docs/cvcpkg-remote-builders.md` and
`docs/cvcpkg-builder-wsl-debian.md` carry the same rule for hand-provisioned
hosts.

The point of the exercise is **minority platforms**: HaikuPorts ships
`click` 8.1.3 and `pyyaml` 6.0 but pins `cryptography` at 3.4.8 against a
`>=41` floor, `sqlalchemy` at 1.3.24 against `^2.0`, and has no `greenlet`
or `httpx` port at all — so four packages the client never imported were the
entire reason `pip install cvcpkg` failed there. It no longer does. See
`docs/haikuports-integration.md`.

### Upload cap is a setting, default 4 GiB (2026-08-02)

The server's maximum bundle size is now a first-class setting —
`cvcpkg server run --max-upload-bytes 8GB` or `CVCPKG_MAX_UPLOAD_BYTES` —
and defaults to **4 GiB**, up from a hard-coded 1 GiB (documented as
512 MiB; the docs had drifted from the code). Sizes accept a byte count
or a human suffix (`4GB`, `512MB`, `2TB`; units are binary), shared with
`CVCPKG_POPULATE_MAX_PACKAGE_BYTES`, which previously crashed on any
suffixed value. The old cap was below the largest bundles we publish —
the CUDA runtime wheels unpack to ~2 GiB. Parsing lives in
`cvcpkg.server.limits` so the CLI can validate the flag without importing
`server.app` (which would freeze its cap before uvicorn loads it); an
unparseable env var falls back to the default rather than refusing to
boot, while a bad CLI flag is a startup error.

### Python packaging: uniform per-interpreter columns (2026-08-02)

Every Python package is now a **per-interpreter column recipe** —
`<name>-cp311 / -cp312 / -cp313 / -cp313t` — pure wheels included; the
bare-name + cross-interpreter copy-fanout model (#389/#390) is retired.
Each column depends on *its* interpreter and its deps' matching columns
and installs only into its own `lib/pythonX.Y[t]/site-packages`, so the
dependency graph is honest (no more `python312` dragged in by every pure
wheel next to `python311` from every abi3 one), the free-threaded
(no-GIL) column exists wherever the whole closure ships cp313t wheels,
and adding a future `python314` is a new column, not a global republish.
`tools/gen_python_recipes.py` emits the entire matrix (poetry.lock
closure + curated seeds: pytest, black, setuptools, sympy, ...), prunes
non-viable columns transitively, and script-shipping tools declare
`provides: [<base>]` (mutually exclusive per prefix; bare-name install
sugar). The C++ `protobuf` recipe, clobbered by the generated Python
wheel of the same name, is restored — the Python columns are
`protobuf-cpNNN`. `pyside6`/`shiboken6`/`triton`/`nvidia-*-cu12` are
renamed to their explicit `-cp311` columns; `ruff` is a plain tool (no
runtime interpreter dep); `python313t` finally packages pip (as
`pip3.13t`). A new `python` meta owns `bin/python`/`bin/pip`; the
`python3` meta keeps `python3`/`pip3`/`python3-config`; activate no
longer synthesizes interpreter aliases behind the metas' back. The
concrete matrices grew to match: torch-cp312/-cp313/-cp313t (CPU wheels;
the cp313t column is gated by the build's no-GIL assertion),
h5py-cp312/-cp313 (from source vs cvcpkg hdf5), pyside6/shiboken6
-cp312/-cp313 (from source vs cvcpkg qt6), and wand-cp313t.

> **The project is now named `cvcpkg`.**  The `libcvc-deps` name is
> retired; the PyPI distribution, CLI, server, and recipe archive are all
> `cvcpkg`.  Backward-compat shims remain where downstream depends on the
> old name (e.g. `find_package(libcvc-deps)` still works via a generated
> compat config).

### Release-readiness changes (cvcpkg tool)

Landed while hardening 2.0.0 for its first `pip install cvcpkg`:

- **`cvcpkg doctor`** — diagnose the local toolchain (Python, CMake, Ninja,
  a C/C++ compiler, git) and optional server reachability.
- **`cvcpkg init`** — scaffold a schema-valid recipe (recipe.yaml + build
  scripts) for cmake / meson / autotools.
- **`cvcpkg upgrade`** — upgrade installed components to newer catalog
  versions in place (with `--dry-run`), updating the lockfile.
- **`cvcpkg install --require-signatures`** — enforce a valid Ed25519
  signature on every installed archive (unsigned = hard failure).
- **`cvcpkg install` writes `cvcpkgConfig.cmake`** into the prefix so
  downstream `find_package(cvcpkg CONFIG REQUIRED)` (or the `libcvc-deps`
  compat name) works with no manual `CMAKE_PREFIX_PATH`.
- **Server admin CLI** — `cvcpkg server stats`, `cvcpkg server backup`
  (sqlite/pg_dump/mysqldump), and `cvcpkg builder logs`.
- **Duplicate-publish gating** — the server returns HTTP 409 on a duplicate
  package variant at the store layer.
- **PostgreSQL recipes** — `postgresql-server` and `postgresql-client`
  (built from the same Meson tree as `libpq`).
- **Packaging fix** — the PyPI publish workflow now actually bundles the
  recipe files into the wheel/sdist (previously shipped zero recipes), and
  verifies the built wheel contains them.
- **Stability fixes** — file-backed SQLite uses `NullPool` to end an
  intermittent "no active connection" teardown failure on macOS CI; the
  Docker integration job's recipes mount was corrected.

### Highlights (2.0.0 infrastructure, 2026-06-06)

The `cvcpkg` 2.0 line is the first to drive the public
catalog at <https://cvcpkg.org> from a SQLModel/Alembic-managed
database (Phase 1 of the [cvcpkg 2.0 roadmap](docs/roadmap/cvcpkg-2.0.md))
instead of YAML state files.

### Highlights

- **Distributed builder fleet.**  Builders run as long-lived agents
  (`cvcpkg builder run`) that pull jobs from the server, register
  per-platform capabilities, and publish artifacts back via the
  authenticated API.  Includes builder pause/resume/cancel, log
  streaming (`builds follow-dag`), `--wait` flag on submit, and a
  public read endpoint for builder info.
- **DAG-based build submission.**  `builds submit-dag` schedules a
  full dependency graph across builders, with correct platform/arch
  pairing and automatic cross-platform fan-out.
- **wasi platform support.**  New `wasi-sdk` recipe (cross-toolchain)
  and wasi/wasm32 builds of zlib, zstd, libjpeg-turbo, yaml, xz, lerc.
  `--cross-platform wasi` is recognized by the builder.
- **CLI restructure.**  The 6,700-line `cli.py` is split into a
  `cli/` package (`_build`, `_publish`, `_install`, `_catalog`,
  `_server`, `_builder`, `_builds`, `_signing`, `_recipe`,
  `_webhook`, `_cache`, `_helpers`).  Default `recipes-dir` resolved
  automatically with a `--no-default-recipes` opt-out.
- **Recipe schema gains `runtime` deps and `cross_toolchain`.**
  `emsdk` advertises wasm tooling; the builder auto-installs host
  tools for cross-compilation and splits build vs. runtime deps.
- **Server URL fix.**  `_install_deps` / `_install_cross_toolchains`
  now prepend the configured server base URL to relative
  `/v1/download/...` paths (fixed 27 build failures from
  `UnsupportedProtocol`).
- **Dev/prod deploy workflows.**  Builder restart + auto-update in
  `deploy-dev` and `deploy-prod`; `sandipaws` Windows builder
  registered and updated via SSH from `star-00`.
- **Recipe push permissions.**  `publisher` role can now push
  recipes; populate workflow timeout raised to 2h.

No new upstream component pins are introduced; v2.0.0 is purely an
infrastructure release.  The catalog at `https://cvcpkg.org/v1/catalog`
serves the recipes pinned at recipe commit `6631d06`.

---

## v1.6.1 (2026-05-31)

BSD-focused bug-fix release.

- **NetBSD port complete.**  Port 7 failing recipes to NetBSD; add
  `lz4`, `readline`, `gettext` recipes.  Use `pkg_add`, fix static
  OpenSSL linking, ship a `python3` shim.
- **NetBSD CI hardening.**  Serialize `pkgin` with `flock` (lockf
  is FreeBSD-only) to prevent concurrent DB corruption, retry
  `pkgin update` if the DB is empty after the first attempt.
- **BSD post-build fixes.**  Export `BASE_DIR` to `GITHUB_ENV` so
  it's visible across steps under `set -u`; use `-G 'Unix Makefiles'`
  instead of `-G Ninja` for the marker cmake step because
  `/usr/pkg/bin/ninja` on NetBSD is the IRC client.

---

## v1.6.0 (2026-05-29)

Windows self-hosted runners, recipe schema upgrades, and the
`platform: any` recipe class.

- **Self-hosted Windows runners for static builds.**  Bypass GitHub
  runner timeouts on heavy Windows static jobs.  Bootstrap `vcpkg`
  if missing; add Git bash, PowerShell 7, Chocolatey bin, and the
  full Machine PATH to `GITHUB_PATH` so bash steps see choco-installed
  Python/NASM/etc.
- **`platform: any`.**  Recipes that produce platform-independent
  artifacts (header-only, scripts) declare `platform: any` and are
  built once and reused everywhere (#72).
- **Recipe schema: runtime deps + cross_toolchain.**  Build vs.
  runtime deps split; auto-build host tools for cross-compilation;
  cross-toolchain discovery so a single submit can pull in `wasi-sdk`,
  `emsdk`, etc.
- **Build log streaming.**  `cvcpkg builds follow-dag` tails live
  build logs over the server API.
- **wasm32 cross-compilation hardening.**  Multiple OpenSSL wasm
  fixes (AR line splitting under cmd.exe's 8191-char limit, keep
  `sh.exe` on PATH, only post-process `Makefile` not
  `configdata.pm`).
- **Server cache backend.**  Modernized release pipeline, server-side
  build cache, Qt6 built from source on all platforms.
- **Daemon mode + graceful shutdown + org ACL.**  Server runs as a
  proper daemon; identifier validation tightened.
- **riscv64 + other architecture support.**  Architecture is now a
  free-form string throughout cvcpkg (`riscv64`, `ppc64le`, `armv7l`,
  …) so new targets land without schema changes.
- **Landing page redesign.**  Tags as filter dropdown + grid view
  (#78); mobile burger menu fixes (#73); configurable branding;
  guide page; package detail pages show install `--prefix`; package
  count is distinct names.
- **Publish workflow.**  Merge `push` into `publish`; `publish`
  accepts recipe names instead of archive paths; `pack-all`
  cleanup; `clean` command for orphaned work dirs.
- **Infra.**  Backend container memory raised from 1G to 8G.

---

## v1.5.0 (2026-05-26)

WASM as a first-class platform.

- **WASM build matrix.**  26 recipes gain WASM build-matrix entries
  with host-platform sharding: Linux, macOS, and Windows hosts can
  all produce wasm32 artifacts.  `qt6-wasm` is unified into the
  main `qt6` recipe.
- **Self-hosted Linux/wasm runners.**  Both GitHub-hosted and
  self-hosted runners participate in Linux/wasm builds for capacity.
- **New host tooling recipes.**  `cmake`, `ninja`, `meson`, `flex`
  are now first-class recipes so cross-compilation hosts can pin
  reproducible toolchains.
- **Yanked-package handling.**  Yanked packages are hidden from the
  catalog API and are no longer uploaded to GitHub Releases.
- **Multi-backend DB tests.**  MySQL multi-backend tests run with
  `spawn` instead of `fork` to avoid the aiomysql greenlet bug
  (xfailed where the bug is unresolvable upstream); shared HMAC key
  volume between backend and test containers.
- **Docker integration tests.**  Stop shelling out to `docker exec`;
  force-rebuild of the test image in CI.

---

## v1.4.1 (2026-05-25)

Incremental-publish + upload-resilience point release.

- **Incremental per-platform publish.**  Each `package-*` matrix job
  publishes its bundles immediately after packaging instead of
  waiting for the whole matrix.  If one platform fails, the others
  still ship.  A final `publish-to-server` job sweeps up any
  stragglers.
- **Chunked / resumable uploads + stream-to-disk** at `/v1/publish`
  for large artifacts.
- **Production tuning.**  Workers reduced to 1 in prod to fix
  chunked-upload session loss; rate limit raised to 300 RPM; max
  upload raised to 1 GiB.
- **GitHub Releases.**  Strip top-level directory from zip archives
  during packaging; tolerate GitHub rate-limit errors in the release
  job; continue publishing remaining archives on individual failures.
- **CLI.**  Wire config/fallback into `install`, add `--source` flag;
  read `__version__` from package metadata instead of a hardcoded
  string.

---

## v1.4.0 (2026-05-25)

Production-deployment release.  The cvcpkg server moves from a
single-node lab process to a Docker + PostgreSQL deployment with a
public landing page at `pkg.tx.wtf` (later `cvcpkg.org`).

- **Docker + PostgreSQL production stack.**  CI-driven `deploy-prod`
  workflow on `prod` branch push; inline docker deploy; `.env.production`
  fetched from the host into the checkout workspace.
- **Production hardening.**  CORS, rate limiting, upload size limits,
  graceful shutdown, structured logging, DB pool improvements.
- **Landing page.**  Bulma-based landing page with package index,
  search, and release-channel tracking.
- **`cvcpkg publish` command + E2E lifecycle tests.**  First-class
  CLI publish flow with end-to-end test coverage.
- **Source-build fallback.**  When a prebuilt binary is unavailable,
  the client falls back to building from source.
- **Multi-backend DB tests.**  SQLite + PostgreSQL + MySQL fixture
  refactor; convenience scripts.
- **CI gating.**  PR-triggered jobs gated to `transfix` actor via
  `CI_ALLOWED_ACTORS` repo variable.
- **GitHub repo references parameterized** throughout the recipes
  and server so forks can deploy their own catalog.
- **Alembic migrations, metrics, and docs.**  All remaining
  production gaps closed for the v1.x line.

---

## v1.3.0 (2026-05-24)

**Split-distribution release.**  The first release built around
per-component archives rather than monolithic per-platform zips.
Establishes the recipe-driven architecture that the rest of the 1.x
and 2.x line builds on.

- **Per-recipe split packaging.**  25+ independent component bundles
  per platform/config/link combo, published as individual archives
  with their own metadata.  Consumers pull only what they need.
- **Ed25519 package signing and verification** (#38).  Optional
  signature + key fingerprint fields in manifests; signing pipeline
  scaffolding in `cvcpkg signing`.
- **Windows builds fully working.**  vcpkg used for GSL, OpenBLAS,
  and ImageMagick (replacing source/CMake builds); gRPC and its
  ecosystem (protobuf, abseil, re2) built as static libs with MSVC
  CRT alignment (`/MD`); NASM assembler installed; Strawberry/Git
  PATH entries removed so they don't shadow MSVC `link.exe`; OpenSSL
  Perl path fixed; abseil CRT mismatch fixed.
- **Release pipeline fixed** to upload per-component bundles + their
  indexes to the GitHub Release alongside the monolithic zips.
- **cvcpkg 2.0 roadmap.**  `docs/roadmap/cvcpkg-2.0.md` (#36) lays
  out the daemon-centric registry design with trust, identity, and
  multi-platform expansion.
- **BSD VM provisioning tools.**  Self-contained scripts to provision
  FreeBSD, OpenBSD, and NetBSD build VMs on the Incus cluster
  (`star-01`/`star-00`); README with lessons learned.
- **New recipes.**  JPEG, LZMA, WebP, zstd, LERC added so libtiff
  can re-enable its full codec set.
- **macOS Qt6.**  Resolve symlinks in Qt6 build dir; resolve symlinks
  in builder paths so Qt6 macOS builds succeed.

---

## v1.1.0 (2026-05-19)

Feature release adding the pieces needed to move TexMol and other
science applications off in-tree dependency copies and onto the shared
libcvc-deps distribution. The v1.1.0 artifacts are based on the v1.0.2
manifest, with these additions and notable packaging fixes:

- **levmar 2.6 added on all platforms.** Built from the vendored
  upstream 2.6 sources with LAPACK enabled and exported as
  `levmar::levmar`.
- **Windows: pthreads4w added.** The vcpkg `pthreads` port is staged so
  projects that include `<pthread.h>` can continue using CMake's normal
  `find_package(Threads)` / `Threads::Threads` target.
- **macOS: Boost header-only component config stubs.** Homebrew's Boost
  1.90 bottle omits several `boost_<component>-1.90.0` CMake package
  directories for header-only components such as `boost_system`. The
  bundle now synthesizes stubs forwarding those components to
  `Boost::headers`, so downstream `find_package(Boost COMPONENTS
  system ...)` works against the extracted archive.
- **Linux: HDF5 staging hardened.** The stage step now skips recursive
  self-referential symlinks in Ubuntu's HDF5 serial layout while still
  copying the real libraries and package metadata.
- **libyaml 0.2.5 added on all platforms.** Downstream projects can use
  `find_package(yaml CONFIG REQUIRED)` and link the `yaml` target for
  YAML configuration parsing/emitting.
- **Protocol Buffers + gRPC added on all platforms.** Downstream
  transport layers can use `find_package(Protobuf CONFIG REQUIRED)` and
  `find_package(gRPC CONFIG REQUIRED)`, then link
  `protobuf::libprotobuf` and `gRPC::grpc++`. Code-generation tools
  such as `protoc` and `grpc_cpp_plugin` are staged in `bin/`. On the
  Windows `*-static` bundle the Protobuf + gRPC stack (plus its
  transitive support libraries — Abseil, c-ares, OpenSSL, RE2, upb,
  utf8_range, zlib) is shipped as a shared `.dll` + import `.lib`
  fallback, mirroring how CGAL/GMP/MPFR are handled on the same
  bundle; the public CMake target surface is identical to the shared
  bundle (see `docs/known-issues.md`, "Windows static builds: grpc
  hang in vcpkg").
- **Linux: HDF5 / libtiff development layout completed.** The bundle
  now provides conventional versioned HDF5 aliases such as
  `libhdf5.so.<abi>` in addition to Ubuntu's `libhdf5_serial.so.<abi>`
  names, and stages the full public libtiff header surface plus
  relocated `libtiff*.pc` metadata for downstream projects that include
  libtiff directly.

### Full component manifest — v1.1.0

| Component | Linux (Ubuntu 24.04 apt) | macOS (arm64 Homebrew) | Windows (vcpkg / aqtinstall / MSYS2) |
|---|---|---|---|
| Boost | 1.83.0 | 1.90.0 | 1.90.0 (vcpkg) |
| HDF5 | 1.10.10 | 2.1.1 | 2.1.1 (vcpkg) |
| FFTW3 | 3.3.10 | 3.3.11 | 3.3.11 (MSYS2 `mingw-w64-x86_64-fftw`) |
| GSL | 2.7.1 | 2.8 | 2.8 (vcpkg) |
| log4cplus | 2.1.2 (source) | 2.1.2 | 2.1.2 (source) |
| libtiff | 4.5.1 | 4.7.1 | 4.7.1 (vcpkg `tiff`) |
| CGAL | 5.6 | 6.1.1 | 6.1.1 (vcpkg) |
| GMP | 6.3.0 | 6.3.0 | 6.3.0 (vcpkg) |
| MPFR | 4.2.1 | 4.2.2 | 4.2.2 (vcpkg) |
| ImageMagick | 6.9.x Q16 (apt) | 7.1.2-21 Q16HDRI | 7.1.2-21 Q16-HDRI (vcpkg overlay) |
| Qt | 6.4.2 (`qt6-base-dev`) | 6.11.0 (brew `qt@6` bottle) | 6.7.3 (`install-qt-action` `6.7.*`) |
| VTK | 9.5.0 (source) | 9.5.2 (brew bottle) | 9.5.0 (source) |
| NFFT3 | 3.5.3 (apt `libnfft3-dev`) | 3.5.3 (source) | 3.5.3 (source) |
| Eigen3 | 3.4.0 | 5.0.1 (brew bottle) | 3.4.x (vcpkg) |
| BLAS / LAPACK | Ubuntu reference 3.x | 3.12.1 (brew `lapack`) | OpenBLAS 0.3.29 (vcpkg) |
| vcglib | 2025.07 | 2025.07 | 2025.07 |
| libiimod | LabShare-Archive/IMOD `8c592ce4` | same | same |
| **levmar** | **2.6 (vendored source)** | **2.6 (vendored source)** | **2.6 (vendored source)** |
| **libyaml** | **0.2.5 (`libyaml-dev`)** | **0.2.5 (Homebrew `libyaml`)** | **0.2.5 (vcpkg `libyaml`)** |
| **Protobuf** | **3.21.12 (`libprotobuf-dev`)** | **34.1 (Homebrew `protobuf`)** | **6.33.4 (vcpkg `protobuf`)** |
| **gRPC** | **1.51.1 (`libgrpc++-dev`)** | **1.80.0 (Homebrew `grpc`)** | **1.76.0 (vcpkg `grpc`)** |
| **Abseil** | **20220623.1 (`libabsl-dev`)** | **20260107.1 (Homebrew `abseil`)** | **20260107.1 (vcpkg `abseil`)** |
| **c-ares** | **1.27.0 (`libc-ares-dev`)** | **1.34.6 (Homebrew `c-ares`)** | **1.34.6 (vcpkg `c-ares`)** |
| **OpenSSL** | **3.0.13 (`libssl-dev`)** | **3.6.2 (Homebrew `openssl@3`)** | **3.6.2 (vcpkg `openssl`)** |
| **RE2** | **20230301 (`libre2-dev`)** | **2025-11-05 (Homebrew `re2`)** | **2025-11-05 (vcpkg `re2`)** |
| **zlib** | **1.3 (`zlib1g-dev`)** | **macOS SDK / Homebrew dependency** | **1.3.2 (vcpkg `zlib`)** |
| **pthreads4w** | **n/a** | **n/a** | **vcpkg `pthreads`** |

(Components added or changed since v1.0.2 are shown in bold.)

### Pin-source notes for v1.1.0

- levmar 2.6 upstream tarball SHA256:
  `3bf4ef1ea4475ded5315e8d8fc992a725f2e7940a74ca3b0f9029d9e6e94bad7`.
  The source files are vendored under `third-party/levmar/upstream/`
  because the upstream HTTPS endpoint's certificate chain is unreliable
  on GitHub-hosted runners.
- levmar is installed as a static library in every archive flavor. Its
  exported `levmar::levmar` target links `LAPACK::LAPACK` transitively,
  so consumers do not need to remember the LAPACK link dependency.
- On Windows, levmar resolves LAPACK through vcpkg `clapack`'s CONFIG
  package (`lapack` + `f2c`) rather than the system FindLAPACK module.
  This avoids probing vcpkg `openblas.lib` for LAPACK symbols that are
  not present in the MSVC OpenBLAS build.
- Linux's `libyaml-dev` package does not ship an upstream CMake package,
  so libcvc-deps generates a small compatible `yamlConfig.cmake` that
  exposes both `yaml` and `yaml::yaml` targets. macOS and Windows use
  the package metadata provided by Homebrew / vcpkg.
- Protobuf and gRPC are intentionally consumed from each platform's
  package manager. Their CMake package names and target names are the
  upstream ones (`Protobuf`, `gRPC`, `protobuf::libprotobuf`,
  `gRPC::grpc++`), while platform-specific transitive dependencies
  such as Abseil, c-ares, OpenSSL, RE2, utf8-range, and zlib are staged
  alongside them so downstream projects can configure without installing
  those development packages separately.
- Protobuf support libraries such as `upb` and `utf8_range` are staged
  when the platform package manager exposes them as installed libraries
  or CMake/pkg-config metadata. They are treated as implementation
  support for Protobuf/gRPC rather than as a primary downstream API.

---

## v1.0.2 (2026-05-14)

Point release fixing the macOS volrover3 bundle and tightening the
Linux Qt6 layout discovered while exercising the v1.0.1 artifacts in
libcvc's release pipeline. **No upstream component versions changed
from v1.0.1; the package manifest is identical** — see the
[v1.0.0 manifest](#full-component-manifest--v100).

Fixes:

- **VTK Python wrapping disabled (#22).** Homebrew's `vtk` bottle
  links against `libpython3.13.dylib` from the brew Python keg.
  Consumers building libcvc on a runner that doesn't carry that
  exact Python were failing the macOS volrover3 link. The bundled
  VTK is now built with `VTK_WRAP_PYTHON=OFF` on every platform,
  removing the Python runtime dependency entirely. (Python wrapping
  was never used by libcvc or volrover3.)
- **Linux: Qt6 mkspecs at multiarch path (#21).** `Qt6CoreConfig.cmake`
  resolves `QT_HOST_DATA_DIRS` relative to its own location and then
  expects `mkspecs/` underneath. Apt installs the mkspecs at
  `/usr/share/qt6/mkspecs`, so the bundle now mirrors them under
  `lib/x86_64-linux-gnu/qt6/mkspecs/` to match.
- **Linux: Qt6 headers at multiarch include path (#20).** Same kind
  of relative-path issue: `Qt6CoreTargets.cmake` resolves
  `INTERFACE_INCLUDE_DIRECTORIES` to
  `<prefix>/include/x86_64-linux-gnu/qt6/QtCore`. Headers are now
  staged under `include/x86_64-linux-gnu/qt6/` instead of plain
  `include/qt6/`.
- **Linux + macOS: Boost shared libraries at the multiarch path,
  macOS install_name rewrite (#19).** Bundles the Boost SOs at
  `lib/x86_64-linux-gnu/` and rewrites the macOS install_names of
  bundled Boost dylibs to `@rpath/<dylib>` so the relocatable layout
  actually works from the consumer's extract location.
- **Linux: Boost cmake configs at multiarch path (#18).** Mirrors
  `BoostConfig.cmake` + `Boost*-1.83.0.cmake` etc. under
  `lib/x86_64-linux-gnu/cmake/Boost-*` so the `_IMPORT_PREFIX` walk
  in the generated targets file resolves to the correct prefix.
- **Windows: ship Qt6 release + debug variants side-by-side in both
  bundles (#17).** The Windows Debug bundle was previously pruning
  every non-`d`-suffixed Qt file (`Qt6Core.dll`, `Qt6Core.lib`, the
  imageformats / platforms / styles plugins, …). Qt6's CMake exports
  are multi-config and remap `RELEASE` / `MINSIZEREL` to
  `RELWITHDEBINFO` at `find_package` time, so `find_package(Qt6)` in
  consumer projects always tried to resolve the missing non-suffixed
  paths. The prune step is gone; both variants now ship in both
  bundles.
- **macOS CI: batch `dylibbundler` + cache brew bottles + cache NFFT3
  install prefix (#16).** No artifact change, but the macOS shared
  release jobs are now much faster on warm caches (was ~35-45 min
  per job, now ~5-8 min on a hit). See the PR for the breakdown.

---

## v1.0.1 (2026-05-13)

First point release. **Same upstream component manifest as v1.0.0**;
fixes target the Linux artifact layout and add per-config VTK +
log4cplus builds so the Debug Linux bundle no longer mixes release
binaries.

Fixes:

- **Linux: build VTK + log4cplus per `matrix.build_type` (#12).**
  The Linux Release and Debug bundles were both pulling VTK and
  log4cplus from a single cached install prefix, so the Debug
  archive shipped Release-compiled `libvtk*.so` / `liblog4cplus.so`.
  Both libraries are now built separately for each
  `matrix.build_type` and cached under per-build-type keys.
- **Linux: mirror Qt6 SOs to the multiarch path + bundle
  `BoostDetectToolset` (#13).** Stages the Qt6 SOs under
  `lib/x86_64-linux-gnu/` so `Qt6CoreConfig.cmake`'s
  `find_library(... PATHS "${_IMPORT_PREFIX}/lib/x86_64-linux-gnu")`
  resolves, and ships Boost's `BoostDetectToolset.cmake` so
  `find_package(Boost)` works without falling back to a system Boost.

---

## v1.0.0 (2026-05-13)

Initial release. Versions actually shipped in the `v1.0.0` release
artifacts. Where a platform's package manager picked a different
upstream version than the workflow's nominal pin (e.g. Homebrew's
VTK bottle), the shipped version is recorded here verbatim.

### Full component manifest — v1.0.0

| Component | Linux (Ubuntu 24.04 apt) | macOS (arm64 Homebrew) | Windows (vcpkg / aqtinstall / MSYS2) |
|---|---|---|---|
| Boost | 1.83.0 | 1.90.0 | 1.90.0 (vcpkg) |
| HDF5 | 1.10.10 | 2.1.1 | 2.1.1 (vcpkg) |
| FFTW3 | 3.3.10 | 3.3.11 | 3.3.11 (MSYS2 `mingw-w64-x86_64-fftw`) |
| GSL | 2.7.1 | 2.8 | 2.8 (vcpkg) |
| log4cplus | 2.1.2 (source) | 2.1.2 | 2.1.2 (source) |
| libtiff | 4.5.1 | 4.7.1 | 4.7.1 (vcpkg `tiff`) |
| CGAL | 5.6 | 6.1.1 | 6.1.1 (vcpkg) |
| GMP | 6.3.0 | 6.3.0 | 6.3.0 (vcpkg) |
| MPFR | 4.2.1 | 4.2.2 | 4.2.2 (vcpkg) |
| ImageMagick | 6.9.x Q16 (apt) | 7.1.2-21 Q16HDRI | 7.1.2-21 Q16-HDRI (vcpkg overlay) |
| Qt | 6.4.2 (`qt6-base-dev`) | 6.11.0 (brew `qt@6` bottle) | 6.7.3 (`install-qt-action` `6.7.*`) |
| VTK | 9.5.0 (source) | 9.5.2 (brew bottle) | 9.5.0 (source) |
| NFFT3 | 3.5.3 (apt `libnfft3-dev`) | 3.5.3 (source) | 3.5.3 (source) |
| Eigen3 | 3.4.0 | 5.0.1 (brew bottle) | 3.4.x (vcpkg) |
| BLAS / LAPACK | Ubuntu reference 3.x | 3.12.1 (brew `lapack`) | OpenBLAS 0.3.29 (vcpkg) |
| vcglib | 2025.07 | 2025.07 | 2025.07 |
| libiimod | LabShare-Archive/IMOD `8c592ce4` | same | same |

### Pin-source notes for v1.0.0

- `VTK_VERSION: 9.5.0`, `QT_VERSION_WINDOWS: 6.7.*`,
  `VCGLIB_VERSION: 2025.07`, `LOG4CPLUS_VERSION: 2.1.2`,
  `NFFT_VERSION: 3.5.3` are set in `.github/workflows/release.yml`.
- vcglib SHA256:
  `e49fc9342d5476b3e39a5e1939b965b57c91d7a17b4f97b8c5eaf01228b16cf0`.
- NFFT3 source tarball SHA256:
  `caf1b3b3e5bf8c33a6bfd7eca811d954efce896605ecfd0144d47d0bebdf4371`.
- libiimod is built from
  [LabShare-Archive/IMOD](https://github.com/LabShare-Archive/IMOD)
  commit `8c592ce4cfae5e0748314da56d73334de7465776` (archived,
  read-only since 2018-07-15).
- macOS Homebrew's `vtk` bottle currently ships 9.5.2 even though the
  workflow's nominal VTK pin is 9.5.0; the Linux and Windows builds use
  the pinned 9.5.0 source.
- macOS Homebrew's `eigen` bottle is on the 5.x release line; the
  Linux/Windows builds remain on the 3.4 series. Consumer code should
  not rely on Eigen ABI parity across platforms in v1.0.0.
- Linux Ubuntu 24.04 ships ImageMagick 6 (Q16) via apt while macOS and
  Windows ship ImageMagick 7. Code that includes `<Magick++.h>` builds
  on both, but ABI differs.
