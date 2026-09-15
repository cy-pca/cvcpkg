# Roadmap: split `libcvc-deps` into composable component bundles

Status: **Implemented** (shipped in v1.3.0) — retained as a historical
design record.

> **Do not renumber sections.** Code docstrings cite this document's
> §-numbers directly: `src/cvcpkg/abi.py` (§3.2.1), `src/cvcpkg/lockfile.py`
> (§5.4), `src/cvcpkg/builder.py` (§7.4–7.5 and §7.3), and
> `src/cvcpkg/config.py` (§5.9.3). Section numbering must stay stable.

Author: roadmap drafted 2026-05-19 in response to Windows monolithic
bundles tipping over 2 GB (PowerShell `Compress-Archive`'s 2 GB cap
was hit on every Windows assemble flavor in run `26127717492`, and
the 7z workaround in commit `af27d59` only buys headroom — the
underlying problem of a single ~2 GB+ artifact per platform/config
will keep growing as we add gRPC, MKL, additional VTK modules, etc.).

The goal of this document is to describe **what** the split looks
like and **how** downstream consumers (and developers) put the
pieces back together into a single coherent install prefix, with no
implementation work performed yet.

## 1. Motivation

The current `libcvc-deps` ships one archive per `(platform, arch,
build_type, link)` tuple — eight archives in total for v1.1.0 (Linux
× 2, macOS × 2, Windows × 4). Every archive is a complete install
prefix: Boost, HDF5, FFTW3, GSL, log4cplus, libtiff, libyaml,
Protobuf, gRPC, CGAL+GMP+MPFR, ImageMagick, Qt 6, VTK 9.5, libiimod,
levmar, pthreads4w, plus all transitive support libraries.

This bundling has served well, but it has three growing problems:

1. **Archive size**. Windows bundles are ≥2 GB compressed today and
   growing every release. Even minor additions (e.g. MKL, an
   additional VTK module group, debug PDBs for a new package) push
   the bundle further into territory where tooling — GitHub
   Releases UI, GitHub Actions artifact upload, PowerShell's
   `Compress-Archive`, some corporate proxies — starts to push back.
2. **Download cost for narrow consumers**. A downstream that only
   needs Boost + FFTW + HDF5 still pays the full ~2 GB for VTK and
   Qt6. There is no way for a consumer to opt out of any subset.
3. **Update granularity**. A single fix to one component (e.g. a
   libtiff CVE patch) forces a full re-release and re-download of
   the entire monolithic bundle on all platforms.

A component-bundle distribution addresses all three: ship many
small archives, each one self-describing via a manifest, and let
consumers (or a small tool) materialize the install prefix they
need from a list of requirements.

## 2. Non-goals

This proposal explicitly does **not**:

- Replace `vcpkg`, `conan`, `spack`, or any general-purpose package
  manager. The local tool we propose is intentionally tiny and
  knows only about our component-bundle layout.
- Continue to gate everything on a single `libcvc-deps` release
  version. Each component bundle carries its **own** version
  (derived from its upstream version plus a `libcvc-deps` build
  revision), declares dependencies in terms of **other component
  versions**, and is resolvable across any number of `libcvc-deps`
  releases. The `libcvc-deps` release tag becomes a convenience
  manifest pointing at a curated set of component versions, not a
  hard coupling.
- Change the on-disk layout consumers see after a `--build-prefix`
  is materialized. A downstream's `CMAKE_PREFIX_PATH` still points
  at one directory and `find_package(...)` calls still resolve
  exactly as they do today against a monolithic bundle.
- Change how upstream components are built in CI. The
  `windows-vcpkg`, `windows-vtk`, `linux`, `macos` jobs continue to
  produce the same install trees; only the post-build packaging
  and the release-time artifact set change.

## 3. Distribution shape

### 3.1 Bundle archives

A `libcvc-deps` release publishes a set of **component bundles**.
Each bundle covers one logical component (or tightly-coupled
component group — see §3.3) for one
`(platform, arch, build_type, link)` tuple. Each bundle has its
**own component version**, independent of the `libcvc-deps`
release that introduced it. The archive naming therefore embeds
the component version, not the release version:

```
libcvc-deps-<component>-<component-ver>-<platform>-<arch>-<config>[-<link>].<ext>
```

The component version is `<upstream-version>+cvc.<rev>`, where
`<upstream-version>` is the upstream package's own version and
`<rev>` is an integer that increments whenever we re-build the
same upstream version (patch bump, build-flag change, transitive
dep refresh). Two `libcvc-deps` releases that ship the
bit-identical Boost 1.90.0 build re-use the same
`boost-1.90.0+cvc.1` bundle artifact; only the
`libcvc-deps-<release>-index.yaml` differs.

Examples (a mixed set, all Linux x86_64, Release, shared):

```
libcvc-deps-boost-1.90.0+cvc.1-linux-x86_64-release-shared.tar.gz
libcvc-deps-hdf5-1.14.4+cvc.2-linux-x86_64-release-shared.tar.gz
libcvc-deps-fftw3-3.3.10+cvc.1-linux-x86_64-release-shared.tar.gz
libcvc-deps-grpc-1.76.0+cvc.1-linux-x86_64-release-shared.tar.gz
libcvc-deps-qt6-6.8.2+cvc.1-linux-x86_64-release-shared.tar.gz
libcvc-deps-vtk-9.5.0+cvc.3-linux-x86_64-release-shared.tar.gz
...
```

`+cvc.<rev>` is SemVer build-metadata; for ordering purposes only
the leading `<upstream-version>` participates in version-range
comparisons. The `<rev>` is a tiebreaker used by the resolver
when multiple bundles with the same upstream version are
available (highest `<rev>` wins, unless explicitly pinned).

The `<config>` field (`debug`/`release`) is dropped for
config-agnostic bundles (headers-only, build tools). The
`<link>` field is dropped on platforms where it does not apply
(e.g. macOS-only bundles where we ship one flavor).

A bundle's archive is structurally identical to today's monolithic
bundle — a `prefix/{bin,lib,include,share,...}` tree — but
containing only the files for that one component plus its in-bundle
metadata under `share/libcvc-deps/`.

### 3.2 Manifest

Each bundle ships a manifest at:

```
share/libcvc-deps/manifest.yaml
```

YAML chosen so we can reuse the libyaml dependency we already ship
(no new runtime dep) and because humans read it more easily than
JSON. Schema:

```yaml
schema_version: 3
bundle:
  name: grpc                       # short, lowercase, hyphen-separated
  version: 1.76.0+cvc.1            # component version (upstream+cvc.rev)
  upstream_version: 1.76.0         # convenience copy of the upstream version
  cvc_revision: 1                  # convenience copy of the +cvc.<rev> int
  introduced_in: 1.1.0             # earliest libcvc-deps release shipping this bundle
  last_seen_in: 1.3.0              # latest libcvc-deps release shipping this bundle
  platform: windows                # linux | macos | windows
  arch: x86_64                     # x86_64 | arm64
  build_type: release              # release | debug | none
  link: shared                     # consumer-facing link mode: shared | static | none
  link_actual: shared              # what the bundle actually ships; may differ
                                   # from `link` when upstream cannot be built
                                   # static on this platform (e.g. on Windows
                                   # x64-static, grpc/cgal bundles still ship
                                   # .dll + import .lib fallbacks). Valid:
                                   # shared | static | hybrid.
  triplet: x64-windows             # platform-native triplet/RID (optional)
  abi:                             # ABI compatibility tag (see §3.2.1).
                                   # Resolver enforces a-b compatibility by
                                   # default; bypass with `cvcpkg --ignore-abi`.
    cxx_std: 17                    # 11 | 14 | 17 | 20 | 23
    cxx_runtime: msvc-14.4         # gcc-<major> | clang-<major> | msvc-<toolset>
    libc: msvcrt                   # glibc-<ver> | musl-<ver> | msvcrt | libc++ABI
    crt_link: dynamic              # dynamic | static (Windows /MD vs /MT)
    extra: []                      # free-form additional discriminators

contents:
  description: >
    gRPC C++ runtime, codegen plugins, and the protobuf libraries
    it depends on. Provides CMake CONFIG packages Protobuf and
    gRPC.
  files:
    # Top-level paths (relative to bundle root) that this bundle
    # owns. Used by the local tool for collision detection and
    # for clean uninstall. Globs allowed.
    - bin/protoc.exe
    - bin/grpc_cpp_plugin.exe
    - bin/grpc_*.dll
    - lib/grpc*.lib
    - lib/protobuf*.lib
    - include/grpc/
    - include/grpcpp/
    - include/google/protobuf/
    - share/grpc/
    - share/protobuf/
    - lib/cmake/grpc/
    - lib/cmake/protobuf/
  cmake_packages:
    # find_package CONFIG packages this bundle provides.
    - name: Protobuf
      targets: [protobuf::libprotobuf, protobuf::libprotoc]
    - name: gRPC
      targets: [gRPC::grpc, gRPC::grpc++]
  pkgconfig:
    - protobuf.pc
    - grpc.pc
    - grpc++.pc
  tools:
    # Executables shipped in bin/ that consumers may invoke.
    - protoc
    - grpc_cpp_plugin

dependencies:
  # Other component bundles required at the bundled configuration.
  # Versions are expressed in terms of the dependency component's
  # OWN upstream version (NOT the libcvc-deps release version),
  # so the resolver can satisfy them from any libcvc-deps release
  # that ships a compatible bundle. SemVer range syntax
  # (>=, <, ~>, ^, ==, plus ABI-compat ~=, plus '||' for unions).
  required:
    - name: abseil
      version: ">=20240722,<20260000"      # ABI-stable LTS line
      reason: gRPC ABI requires matching Abseil major
    - name: openssl
      version: "^3.0"                       # any 3.x
    - name: c-ares
      version: ">=1.34.0"
    - name: re2
      version: ">=2024-07-02"
    - name: zlib
      version: "^1.3"
  optional: []

provides:
  # Virtual capabilities other bundles can depend on. Used so e.g.
  # 'hdf5' can depend on 'zlib' without caring whether zlib comes
  # from the 'zlib' bundle or is provided transitively by another
  # bundle that bundled its own copy.
  - protobuf-runtime
  - grpc-runtime

system_requirements:
  # Things that must already be on the host; the local tool
  # surfaces these as a checklist, not as something it installs.
  linux:
    apt:  []
    yum:  []
  macos:
    brew: []
  windows: {}

integrity:
  # Populated at release time by CI.
  sha256: "0000000000000000000000000000000000000000000000000000000000000000"
  size_bytes: 0
  built_at: "2026-05-19T22:00:00Z"
  source:
    type: vcpkg              # vcpkg | apt | brew | vendored | upstream-tarball
    triplet: x64-windows
```

Each release ships one **release index** alongside the bundle
archives:

```
libcvc-deps-1.1.0-index.yaml
```

containing the full list of bundle filenames, their component
versions, their SHA-256s/sizes, and a copy of each bundle's
`manifest.yaml.bundle` and `dependencies` blocks. The index is
additionally curated: it carries a top-level
`recommended:` map giving the exact component version this
release was tested against (e.g. `boost: 1.90.0+cvc.1`,
`grpc: 1.76.0+cvc.1`), so a consumer pinning only the release
version still gets a reproducible install.

In addition, a **catalog** is published that aggregates every
release index ever produced. The catalog is append-only,
versioned, and served from a stable URL on the project's GitHub
Pages site:

```
https://transfix.github.io/libcvc-deps/catalog/latest.yaml      # pointer to newest revision
https://transfix.github.io/libcvc-deps/catalog/<rev>.yaml       # immutable snapshot, rev is monotonic integer
https://transfix.github.io/libcvc-deps/catalog/index.yaml       # list of all rev numbers + sha256s
```

A dedicated workflow runs on every release tag, generates a new
catalog revision by walking the just-published release index plus
all previous indexes, and pushes the result to the `gh-pages`
branch. Properties of the scheme:

- **Append-only**: a new revision `N+1` MUST contain every bundle
  entry from revision `N`, plus zero or more new entries. The
  publish workflow refuses to push a revision that drops or
  mutates an existing entry.
- **Versioned**: each revision is addressable forever at
  `catalog/<rev>.yaml`, and `catalog/index.yaml` lists every
  revision with its sha256 and publish timestamp. A lockfile that
  pins `catalog_revision: 17` can be re-resolved bit-identically
  years later.
- **Signed**: each revision is signed (planned Sigstore / cosign,
  see Follow-up F1) and the signature is published alongside it
  as `catalog/<rev>.yaml.sig`. The tool verifies the signature
  before trusting the catalog content.
- **Stable URL**: `catalog/latest.yaml` always serves the newest
  revision. The tool fetches `latest.yaml` once per run, records
  its `catalog_revision` integer, and uses that exact revision
  for the remainder of the run.

The catalog enables cross-release resolution (§5.5): the tool can
find a component version that satisfies a dependency range even
if no single release shipped exactly that combination. A bundle
artifact is referenced by its GitHub Releases asset URL on the
release that first shipped it, and is immutable once published.

### 3.2.1 ABI compatibility

The `bundle.abi` block in each manifest declares the toolchain
identity the bundle was built against. The resolver, by default,
requires that **every pair of bundles in a resolution be
ABI-compatible** under these rules:

- Same `arch` and `platform` (already enforced by tuple matching).
- `cxx_std`: a bundle declaring `cxx_std: 17` is compatible with
  consumers requesting `>=17`; lower is a hard error.
- `cxx_runtime`: must match family and major version (gcc-11 is
  compatible with gcc-12 only if both are within the same
  declared compatibility window, otherwise hard error).
- `libc`: must match exactly on Linux (glibc bundles cannot be
  mixed with musl); on Windows `msvcrt` must match exactly; on
  macOS `libc++ABI` is assumed.
- `crt_link` (Windows only): `/MD` and `/MT` bundles cannot mix.

`cvcpkg --ignore-abi` (or `accept_abi_mismatch: true` in the
requirements file) downgrades ABI errors to warnings. This is the
escape hatch for downstreams that knowingly accept the risk
(e.g. mixing a tiny header-only bundle across toolchains).

### 3.3 Component grouping

Not every upstream package gets its own bundle. The grouping
principle: **bundle together packages that are always co-installed
because they have hard 1:1 transitive dependencies and no useful
independent use case**. Initial component list:

| Bundle | Contents | Rationale |
|---|---|---|
| `boost` | All Boost libraries we ship | Single upstream release; consumers either need Boost or don't |
| `hdf5` | HDF5 C + C++ + the few transitive bits | Always used together |
| `fftw3` | FFTW3 single + double + threads | Always used together |
| `gsl` | GSL | Standalone |
| `log4cplus` | log4cplus | Standalone |
| `tiff` | libtiff + libdeflate/jpeg if vendored | Always with tiff |
| `yaml` | libyaml | Standalone, small |
| `protobuf` | Protocol Buffers C++ runtime + protoc | Always under gRPC |
| `grpc` | gRPC C++ runtime + plugins | Pulls protobuf, abseil, c-ares, openssl, re2, zlib |
| `abseil` | Abseil | Often a transitive dep of grpc only, but used directly by some downstreams |
| `c-ares` | c-ares | Standalone |
| `openssl` | OpenSSL | Standalone |
| `re2` | RE2 | Standalone |
| `zlib` | zlib | Standalone |
| `cgal` | CGAL + GMP + MPFR + MPIR | These three are useless apart |
| `imagemagick` | ImageMagick Q16-HDRI | Standalone, large |
| `qt6` | Qt 6 Core/Gui/Widgets/OpenGL/OpenGLWidgets | Always together |
| `vtk` | VTK 9.5 (Qt6 enabled) | Depends on qt6 |
| `libiimod` | IMOD's MRC/TIFF subset | Standalone |
| `levmar` | levmar (LAPACK) | Standalone, vendored |
| `pthreads4w` | pthreads4w | Windows-only |
| `nfft3` | NFFT3 | Windows-only (MSYS2-built, staged) |
| `clapack` | clapack + f2c | Windows-only, for levmar's LAPACK |
| `openblas` | OpenBLAS | Standalone |

A future refinement can split `vtk` into a kernel bundle plus
opt-in modules (`vtk-rendering-qt`, `vtk-ioxml`, etc.) but that is
out of scope for the initial split.

### 3.4 Size estimate

Rough breakdown of the current ~2 GB Windows Release/shared bundle:

| Component | Approx size | % of total |
|---|---|---|
| Qt 6 (Core/Gui/Widgets/OpenGL/...) | 700 MB | 35% |
| VTK 9.5 install tree | 500 MB | 25% |
| Boost (all of it) | 250 MB | 13% |
| ImageMagick Q16-HDRI | 150 MB | 8% |
| HDF5 + tiff | 90 MB | 5% |
| gRPC + Protobuf + transitives | 120 MB | 6% |
| CGAL + GMP + MPFR | 40 MB | 2% |
| Everything else combined | 150 MB | 8% |

The biggest wins from splitting:

- A consumer that needs only `boost + hdf5 + fftw3 + tiff` (e.g.
  a headless command-line tool) downloads ~370 MB instead of 2 GB.
- A consumer adding `qt6 + vtk` for visualization pulls another
  ~1.2 GB, but only when they actually want it.
- A grpc-only microservice that doesn't need any of our science
  stack downloads ~130 MB (grpc + protobuf + transitives).

## 4. Materialization layout

Consumers do not run consumer tooling against the bundle archives
directly. They:

1. Either extract one bundle (works exactly like today for
   single-component consumers), or
2. Use the local tool from §5 to materialize a **build prefix**
   that contains several bundles merged into one
   `find_package`-compatible tree.

The build prefix layout matches what a single monolithic bundle
provides today:

```
<prefix>/
  bin/                  # all executables and (on Windows) DLLs
  lib/                  # static + import libs + .so/.dylib
  lib/cmake/<pkg>/      # CMake CONFIG packages
  lib/pkgconfig/        # pkg-config files
  include/              # all headers
  share/                # data, docs
  share/libcvc-deps/
    installed/<bundle>/manifest.yaml   # one per installed bundle
    index.yaml                          # the release index that
                                        # produced this prefix
    lockfile.yaml                       # what bundles+versions are
                                        # installed (see §5.4)
```

This is intentionally identical to today's bundle layout so the
existing `CMAKE_PREFIX_PATH=<prefix>` usage pattern works
unchanged.

### 4.1 Collision policy

When two bundles ship a file at the same path, the local tool:

- **Identical-content collisions** (same SHA-256): silently OK.
  Common for tiny shared headers (e.g. `share/cmake/<...>.cmake`
  fragments injected by vcpkg).
- **Differing-content collisions**: hard error with a manifest-
  diagnostic message listing both owners and the diff path.
  Resolved by tightening one bundle's `contents.files`.

The manifest's `contents.files` declarations are checked at release
time in CI so collisions cannot ship.

## 5. Local management tool: `cvcpkg`

A small Python tool that resolves a requirement set against a
release index, downloads the relevant bundles, and materializes a
build prefix. Lives at the repo root in this repo and is
distributed as a standalone Python package on PyPI for use by lab
collaborators and external downstreams who don't want to clone the
`libcvc-deps` repo just to fetch its bundles.

Design goals: **simple, dependency-light, no daemons, no global
state**. Everything is local to the current working tree.

### 5.1 Packaging and distribution

`cvcpkg` is a Poetry-managed Python package, structured to be
publishable to PyPI:

```
./
  pyproject.toml          # poetry build-system, project metadata, deps, entry point
  README.md               # PyPI-rendered usage docs
  LICENSE                 # mirrors the libcvc-deps top-level LICENSE
  src/
    cvcpkg/
      __init__.py         # exports __version__
      __main__.py         # `python -m cvcpkg` entry
      cli.py              # argparse / command dispatch
      catalog.py          # fetch + verify catalog/<rev>.yaml
      resolver.py         # backtracking SAT-style resolver (\u00a75.5)
      manifest.py         # pydantic / dataclass models for manifest.yaml
      installer.py        # download, verify SHA-256, extract into prefix
      lockfile.py         # read/write lockfile.yaml (\u00a75.4)
      cache.py            # ~/.cache/cvcpkg/<sha256>/ management
      abi.py              # ABI-tag compatibility checks (\u00a73.2.1)
      platform.py         # auto-detect (platform, arch, libc, crt)
      semver.py           # version-range parser (>=, <, ^, ~>, ==, ||)
      errors.py           # typed exceptions
  tests/
    unit/                 # pure-function tests, no network
    integration/          # against fixture catalogs in tests/fixtures/
    fixtures/             # sample catalog/<rev>.yaml + tiny bundle tarballs
```

`pyproject.toml` highlights:

```toml
[tool.poetry]
name = "cvcpkg"
version = "0.1.0"
description = "Component package manager for libcvc-deps prebuilt dependency bundles"
authors = ["CyberPC Angel, LLC"]
license = "MIT"                                # match libcvc-deps
readme = "README.md"
homepage = "https://github.com/transfix/libcvc-deps"
repository = "https://github.com/transfix/libcvc-deps"
documentation = "https://transfix.github.io/libcvc-deps/"
keywords = ["package-manager", "cmake", "scientific-computing", "libcvc-deps"]
classifiers = [
  "Development Status :: 3 - Alpha",
  "Intended Audience :: Developers",
  "Intended Audience :: Science/Research",
  "License :: OSI Approved :: MIT License",
  "Operating System :: POSIX :: Linux",
  "Operating System :: MacOS :: MacOS X",
  "Operating System :: Microsoft :: Windows",
  "Programming Language :: Python :: 3 :: Only",
  "Programming Language :: Python :: 3.10",
  "Programming Language :: Python :: 3.11",
  "Programming Language :: Python :: 3.12",
  "Programming Language :: Python :: 3.13",
  "Topic :: Software Development :: Build Tools",
]
packages = [{ include = "cvcpkg", from = "src" }]

[tool.poetry.dependencies]
python      = "^3.10"
PyYAML      = "^6.0"
# Optional progress bars; falls back to silent if missing.
tqdm        = { version = "^4.66", optional = true }
# Optional Sigstore verification (Follow-up F1); not required for v1.
sigstore    = { version = "^3.0", optional = true }
# Optional storage backends (§5.9).
boto3                  = { version = "^1.34", optional = true }
"google-cloud-storage" = { version = "^2.18", optional = true }
"azure-storage-blob"   = { version = "^12.20", optional = true }
paramiko               = { version = "^3.4",  optional = true }

[tool.poetry.extras]
progress = ["tqdm"]
signing  = ["sigstore"]
s3       = ["boto3"]
gcs      = ["google-cloud-storage"]
azure    = ["azure-storage-blob"]
sftp     = ["paramiko"]
all      = ["tqdm", "sigstore", "boto3", "google-cloud-storage",
            "azure-storage-blob", "paramiko"]

[tool.poetry.group.dev.dependencies]
pytest        = "^8.0"
pytest-cov    = "^5.0"
ruff          = "^0.6"
mypy          = "^1.10"

[tool.poetry.scripts]
cvcpkg = "cvcpkg.cli:main"

[build-system]
requires = ["poetry-core>=1.8.0"]
build-backend = "poetry.core.masonry.api"
```

Install paths for collaborators:

```bash
# Recommended (isolated, latest):
pipx install cvcpkg

# Or:
pip install --user cvcpkg
pip install --user 'cvcpkg[progress]'        # with tqdm
pip install --user 'cvcpkg[all]'             # everything optional

# Or pin a specific version in a project's requirements:
echo 'cvcpkg ~=0.1' >> requirements.txt
```

Release process (separate from the bundle release):

- Tag `cvcpkg-vX.Y.Z` in this repo triggers a `cvcpkg-publish.yml`
  GitHub Actions workflow that runs
  `poetry build && poetry publish --username __token__ --password $PYPI_TOKEN`.
- `PYPI_TOKEN` lives in the repo's Actions secrets, scoped to the
  `cvcpkg` PyPI project only.
- The `cvcpkg` package version is independent of the `libcvc-deps`
  release version; the tool is forward- and backward-compatible
  with any catalog whose `schema_version` is within its declared
  supported range (`cvcpkg.__supported_schemas__ = {1, 2, 3}`).

Runtime/build-time dependencies:

- Python ≥3.10 (already a developer prerequisite in our repos).
- `PyYAML` ^6.0 (single mandatory third-party dependency).
- Stdlib `urllib.request`, `hashlib`, `tarfile`, `zipfile`,
  `argparse`, `concurrent.futures` for the rest.
- Optional extras for non-HTTPS storage backends (§5.9): `boto3`
  (S3), `google-cloud-storage` (GCS), `azure-storage-blob`,
  `paramiko` (SFTP). `rsync` and `rclone` backends use the
  system binary, no Python dep.
- Optional extras: `tqdm` (progress bars), `sigstore` (catalog
  signature verification, see Follow-up F1). The tool degrades
  gracefully when extras are absent.

### 5.2 CLI surface

```
cvcpkg install   [--release <ver>] [--prefix DIR] [--platform P]
                 [--config release|debug] [--link shared|static]
                 [--catalog-revision <rev>] [--ignore-abi]
                 [--from FILE | <component>[==<ver>]...]
cvcpkg add       <component>[==<ver>]...     # add to lockfile + install
cvcpkg remove    <component>...              # remove + uninstall
cvcpkg list      [--installed | --available]
cvcpkg info      <component>
cvcpkg verify                                # re-checksum the prefix
cvcpkg lock                                  # write/refresh lockfile
cvcpkg sync                                  # ensure prefix == lockfile
cvcpkg catalog   [--refresh | --pin <rev> | --show]
cvcpkg gc                                    # prune the local cache
```

`--ignore-abi` disables the default ABI-tag enforcement
(\u00a73.2.1) for that invocation. `--catalog-revision <rev>` pins
the resolver to an exact append-only catalog snapshot for
reproducible builds; without it the tool uses the current
`catalog/latest.yaml` pointer and records the resolved revision
into the lockfile.

Single-file invocations (`cvcpkg install --from requirements.yaml`)
are the recommended workflow for downstream projects so the
required set is checked into the consumer's repo.

### 5.3 Requirements file

A consumer ships a `cvc-requirements.yaml` in their repo. Two
styles are supported and may be mixed:

**Style A — pin a `libcvc-deps` release, take its recommended set:**

```yaml
platform: auto                 # auto | linux | macos | windows
arch: auto                     # auto | x86_64 | arm64
config: release                # release | debug
link: shared                   # shared | static

libcvc-deps: "1.1.0"           # use this release's recommended versions

components:
  - boost
  - hdf5
  - fftw3
  - tiff
  - grpc
```

**Style B — pin per-component versions, ignore the release umbrella:**

```yaml
platform: auto
config: release
link: shared

# No top-level libcvc-deps key. The resolver searches the full
# catalog across all releases.
components:
  - name: boost
    version: "==1.86.0"        # downstream wants old Boost, not the latest
  - name: hdf5
    version: "^1.14"
  - name: fftw3                  # no constraint => latest catalog match
  - name: grpc
    version: "~>1.76"            # 1.76.x, pulled in transitively too
```

**Mixed style — release as a default, with per-component overrides:**

```yaml
libcvc-deps: "1.3.0"           # baseline: use 1.3.0's recommended versions
overrides:
  - name: boost
    version: "==1.86.0"        # but force old Boost
  - name: vtk
    exclude: true              # we don't want VTK at all
components:
  - boost
  - hdf5
  - grpc
```

`cvcpkg install --from cvc-requirements.yaml --prefix ./deps`
resolves against the catalog and produces `./deps/` ready for
`cmake -DCMAKE_PREFIX_PATH=$PWD/deps ...`.

### 5.4 Lockfile

After a successful install the tool writes
`share/libcvc-deps/lockfile.yaml` in the prefix. The lockfile
pins **per-component versions** and records the originating
`libcvc-deps` release of each (so an audit can reproduce where
every bit came from), but the lockfile itself is not tied to a
single release:

```yaml
schema_version: 2
platform: linux
arch: x86_64
config: release
link: shared
resolved_at: "2026-05-19T22:30:00Z"
catalog_revision: 17                       # exact append-only catalog snapshot
catalog_sha256: "..."                      # sha256 of catalog/17.yaml at resolve time

bundles:
  - name: boost
    version: "1.86.0+cvc.2"
    upstream_version: "1.86.0"
    source_release: "1.0.0"                # libcvc-deps release that shipped it
    sha256: "..."
    size_bytes: 261000000
    archive_url: "https://github.com/transfix/libcvc-deps/releases/download/v1.0.0/libcvc-deps-boost-1.86.0+cvc.2-linux-x86_64-release-shared.tar.gz"
  - name: hdf5
    version: "1.14.4+cvc.2"
    source_release: "1.3.0"
    ...
  - name: grpc
    version: "1.76.0+cvc.1"
    source_release: "1.1.0"
    ...
```

A single prefix can — and routinely will — contain bundles drawn
from several `libcvc-deps` releases.

The lockfile is also writable as `./cvc-lock.yaml` next to a
requirements file when the consumer wants to commit it to their
repo. `cvcpkg sync` reads the lockfile and idempotently brings the
prefix to that exact state — this is how CI reproducibly
materializes the same prefix across machines.

### 5.5 Resolution algorithm

The resolver is a small SAT-style backtracking solver over the
catalog. Inputs: the requirements file (top-level constraints,
optional `libcvc-deps:` baseline, `overrides`), the platform tuple,
and the catalog (all bundles from all releases for that tuple).

For each component, the **candidate set** is the list of all
bundle versions in the catalog that:

1. Match the requested `(platform, arch, config, link)` tuple.
2. Satisfy any user-supplied version constraint.
3. Are not excluded by the requirements file or by a higher-level
   pin in the resolution stack.

Candidates are ordered by preference: (a) the version explicitly
pinned by the user, (b) the version `recommended` by the
`libcvc-deps:` baseline release (if any), (c) the highest
upstream version that satisfies all constraints, (d) within an
upstream version, the highest `+cvc.<rev>`.

```
resolve(requirements, catalog):
    constraints = collect_top_level(requirements)
    return backtrack(constraints, picked={}, stack=[])

backtrack(constraints, picked, stack):
    if all constraints satisfied: return picked
    name = pick_next_component(constraints, picked)
    for cand in candidates(name, constraints, catalog):
        new = picked | {name: cand}
        new_constraints = constraints | cand.dependencies.required
        if conflicts(new_constraints, new):
            continue
        result = backtrack(new_constraints, new, stack+[cand])
        if result is not None: return result
    return None        # backtrack; caller tries the next candidate
```

Conflict detection: a chosen bundle X for component A requires
`B >=2.0`; an already-picked bundle Y for component C requires
`B <2.0`. The solver backtracks and either picks a different X
or a different Y. If no assignment exists the tool prints the
full conflict chain and exits non-zero.

Multi-release mixing model: a single prefix MAY contain bundles
drawn from different `libcvc-deps` releases. The only hard
constraint is that the resolved bundle set must form a
consistent dependency graph. This is what makes the system
robust to:

- A downstream pinning an older `boost` for ABI reasons while
  taking the newest `grpc` from a later release.
- A component being dropped from a future `libcvc-deps` release
  (e.g. `imagemagick` retired in 1.4.0): downstreams that still
  need it pull it from the last release that shipped it (the
  bundle's manifest declares `last_seen_in: 1.3.0`), no
  republishing required.
- A consumer pinning `libcvc-deps: 1.1.0` but selectively
  upgrading a single component to a later release's bundle for a
  bugfix.

The one rule we still enforce: per-component **bundle identity**
is immutable. A given `<name>-<version>` archive must be
bit-identical no matter which release index references it; the
resolver assumes archives are content-addressable by SHA-256.

### 5.6 Cache

Downloaded archives go to a content-addressed cache:

```
~/.cache/cvcpkg/<sha256>/<original-filename>
```

This is shared across all prefixes on the machine; `cvcpkg gc`
prunes archives not referenced by any known lockfile. CI uses
`CVCPKG_CACHE=$RUNNER_TEMP/cvcpkg-cache` plus
`actions/cache@v4` keyed on the lockfile's hash for a one-time
populate-then-cache pattern.

### 5.7 Verification

Every `install`/`sync` action:

1. Fetches `https://transfix.github.io/libcvc-deps/catalog/latest.yaml`
   to learn the current `catalog_revision`. If the lockfile pins
   a `catalog_revision` instead, fetches
   `catalog/<rev>.yaml` directly. Catalogs are content-addressed
   in the local cache so repeated runs are offline-friendly.
2. Verifies the catalog's signature (when Follow-up F1 lands;
   until then verifies sha256 against `catalog/index.yaml`).
3. For each resolved bundle, verifies SHA-256 against the
   catalog entry before extraction. Because bundle artifacts are
   immutable (§5.5), a hash mismatch is always a hard error.
4. After extraction, verifies the bundle's
   `share/libcvc-deps/manifest.yaml` is internally consistent
   (every file listed under `contents.files` exists; no extras).

`cvcpkg verify` repeats step 4 against an already-installed prefix.

### 5.8 Out-of-band fallback

If no bundle in the catalog can satisfy the requirements, the
tool emits a precise diagnostic citing the conflict chain:

```
cvcpkg: ERROR: cannot satisfy requirement 'grpc ~>1.76' together
  with 'abseil <20240722' pinned by user.
  Candidate grpc-1.76.0+cvc.1 (from libcvc-deps 1.1.0) requires
    abseil >=20240722,<20260000
  No other grpc-1.76.x bundle exists in the catalog.
  Suggestion: relax the abseil pin, or pin grpc to an older line.
```

For a missing component:

```
cvcpkg: ERROR: component 'mkl' is not present in any libcvc-deps
  release catalog entry for linux/x86_64/release/shared.
  See https://github.com/transfix/libcvc-deps/releases
```

The tool intentionally does **not** try to fall back to monolithic
bundles, system packages, or vcpkg. Failure is loud and explicit.

### 5.9 Storage backends

The default catalog URL
(`https://transfix.github.io/libcvc-deps/catalog/`) and the
default bundle download URLs (GitHub release assets) are policy,
not architecture. `cvcpkg` must be usable against arbitrary
self-hosted or third-party storage: lab HTTPS mirrors, S3 / GCS /
Azure Blob buckets, an SFTP server on a head node, an
`rsync://` mirror, an internal Artifactory, or a directory on a
shared NFS / SMB mount.

The mechanism is **pluggable storage backends dispatched by URI
scheme**, with no scheme being privileged over any other inside
the resolver/installer.

#### 5.9.1 Backend interface

A storage backend is a small Python class registered against one
or more URI schemes. The full interface is intentionally tiny so
new backends are easy to add:

```python
class StorageBackend(Protocol):
    schemes: ClassVar[tuple[str, ...]]      # e.g. ("https", "http")

    def head(self, uri: str) -> ObjectInfo: ...
        # returns size + optional precomputed sha256/etag

    def open(self, uri: str) -> BinaryIO: ...
        # streaming read; cvcpkg hashes while writing to cache

    def list(self, uri: str) -> Iterable[str]:        # optional
        ...                                           # for `cvcpkg mirror`

    def supports_range(self, uri: str) -> bool:       # for resume
        ...
```

Everything else — sha256 verification, retry/backoff, the cache
in §5.6, progress bars, the resolver in §5.5 — is backend-agnostic
and lives above this layer. A backend never decides whether an
artifact is trustworthy; it only moves bytes. Trust is always
established by sha256 against the signed catalog (§5.7).

#### 5.9.2 Built-in backends

Shipped in the wheel; no extra install required:

| Scheme(s) | Implementation | Notes |
|---|---|---|
| `https`, `http` | stdlib `urllib.request` + `ssl` | Default. Honors `HTTPS_PROXY`, `NO_PROXY`. |
| `file` | stdlib `pathlib` | Local filesystem / NFS / SMB mounts. Used by `cvcpkg pack` to publish into a directory layout. |
| `gh-release` | `urllib` + GitHub REST API | Resolves `gh-release://owner/repo/tag/asset` to the asset's CDN URL. Used by the default catalog when assets are on GitHub Releases. |

Shipped as **optional extras** (so PyPI install is tiny by default):

| Scheme(s) | Extra | Backend |
|---|---|---|
| `s3` | `cvcpkg[s3]` → `boto3` | AWS S3 and any S3-compatible store (MinIO, Ceph RGW, Wasabi, Backblaze B2 via S3 API). Honors standard AWS credential chain + `AWS_PROFILE`. |
| `gs` | `cvcpkg[gcs]` → `google-cloud-storage` | Google Cloud Storage. |
| `azblob` | `cvcpkg[azure]` → `azure-storage-blob` | Azure Blob Storage. |
| `sftp`, `ssh` | `cvcpkg[sftp]` → `paramiko` | SFTP / SCP over OpenSSH. Honors `~/.ssh/config`, agent keys. |

Shipped as **subprocess shims** for tools that already exist in
typical scientific-computing environments — no extra Python dep:

| Scheme(s) | Required binary | Backend |
|---|---|---|
| `rsync` | `rsync` | Streams via `rsync --inplace --partial`. Best on intra-cluster mirrors. |
| `rclone` | `rclone` | Any of the 70+ remotes rclone speaks (B2, Dropbox, OneDrive, Swift, WebDAV, …) via `rclone cat`. |
| `s3-cli` | `aws` | Fallback to `aws s3 cp - -` for environments that don't want `boto3` (e.g., HPC sites with site-managed `aws` only). |

Anything `cvcpkg` cannot fetch with a built-in or extra backend is
still reachable if a `rclone` remote is configured, which covers
essentially every remaining provider.

#### 5.9.3 Configuration: mirrors and catalog overrides

Three orthogonal knobs, in increasing precedence:

1. **Compiled-in defaults** — the upstream
   `transfix/libcvc-deps` catalog + GH-Releases asset URLs.
2. **User config** — `~/.config/cvcpkg/config.yaml`:

   ```yaml
   schema_version: 1

   # Override the catalog source. Same signature/sha256 rules apply.
   catalog:
     primary: https://mirror.cs.utexas.edu/cvc/catalog/
     fallback:
       - s3://cvc-lab-mirror/catalog/
       - gh-release://transfix/libcvc-deps/catalog/latest.yaml

   # Optional: rewrite artifact URLs before fetching. The first
   # rule whose `match:` prefix matches is applied; the catalog's
   # original URL is the final fallback.
   mirrors:
     - match: https://github.com/transfix/libcvc-deps/releases/download/
       rewrite: https://mirror.cs.utexas.edu/cvc/releases/
     - match: https://github.com/transfix/libcvc-deps/releases/download/
       rewrite: s3://cvc-lab-mirror/releases/

   # Per-scheme credentials/options. Backend-specific blocks only.
   backends:
     s3:
       endpoint_url: https://s3.us-east-1.amazonaws.com   # or MinIO URL
       region: us-east-1
       # credentials come from AWS env / profile by default
     sftp:
       known_hosts: ~/.ssh/known_hosts
       identity_file: ~/.ssh/id_ed25519_cvc
   ```

3. **Per-project overrides** — `cvc-requirements.yaml` may set
   `catalog:` and `mirrors:` blocks with the same shape; project
   settings win for that working tree only.
4. **Command-line flags** — `--catalog URL`, `--mirror MATCH=REWRITE`
   (repeatable), and `--backend SCHEME=CLASS` win above everything
   else for ad-hoc runs and CI.

Environment variables provide the same effect for unattended
contexts: `CVCPKG_CATALOG_URL`, `CVCPKG_MIRROR_<N>=match=rewrite`,
plus the standard `AWS_*`, `GOOGLE_APPLICATION_CREDENTIALS`,
`SSH_AUTH_SOCK`, `HTTPS_PROXY`, etc., consumed by the underlying
backends.

#### 5.9.4 Fallback and integrity

Mirror lists are tried in order. A single sha256 mismatch on a
mirror is treated as a hard failure for that URL (it is **not**
silently retried against the next mirror, because that would mask
a poisoned mirror): the tool surfaces the mismatch and only then
falls back. Network-class errors (timeout, 5xx, connection
refused, DNS failure) do fall through to the next mirror. The
sha256 in the (signed) catalog is the only authority on what a
bundle's bytes must be — so a mirror can be public, untrusted,
and still safe to use.

#### 5.9.5 Publishing to alternative backends

`cvcpkg push <bundle.tar.zst> --to <uri>` uploads a built bundle
to any backend that exposes a writable `put(uri, blob)` (a
superset of the read-only `StorageBackend` interface). Combined
with `cvcpkg pack` from §7.4, this lets a lab run its own private
release pipeline:

```bash
# CI for a private fork:
cvcpkg pack    recipes/vtk
cvcpkg push    out/vtk-9.3.0+cvc.1-linux-x86_64-glibc228-release-shared.tar.zst \
               --to s3://cvc-lab-mirror/releases/0.2.0/
cvcpkg catalog publish --rev 0.2.0 --to s3://cvc-lab-mirror/catalog/
```

The catalog itself is just an object in storage; pointing
`config.yaml`'s `catalog.primary` at a different URI is all that's
needed to consume it.

#### 5.9.6 Extending with third-party backends

External packages can ship additional backends and register them
via a Python entry point group:

```toml
# in some third-party project's pyproject.toml:
[project.entry-points."cvcpkg.storage_backends"]
ipfs    = "cvcpkg_ipfs:IPFSBackend"
hf-hub  = "cvcpkg_hf:HFBackend"
```

`cvcpkg` auto-discovers these on startup. This keeps niche
backends (IPFS, IPNS, Hugging Face Hub, lab-specific protocols)
out of the core wheel while still making them first-class — no
patching, no monkey-patching, no shelling out.

#### 5.9.7 Scope decisions

- **No write-back to read-only mirrors.** `cvcpkg` never auto-
  uploads a fetched bundle to a "closer" mirror; that is an
  operator/CI concern (`cvcpkg push`).
- **No bittorrent / p2p in core.** Possible later via an entry-
  point backend; would need a "swarm" abstraction the basic
  interface doesn't model.
- **No credential storage by cvcpkg.** All auth is delegated to
  the underlying backend's native mechanism (AWS profile chain,
  SSH agent, OS keyring via the backend SDK). `cvcpkg` itself
  reads no secrets from `config.yaml` beyond filesystem paths.

## 6. Compatibility with today's monolithic bundles

For at least one release after the split lands, we ship **both**:

- The new component bundles (preferred).
- A meta-bundle named `libcvc-deps-all-...` that is the
  union of all components for that platform/config — i.e. the
  monolithic bundle as it exists today, byte-for-byte.

Consumers using `LIBCVC_DEPS_ROOT` with the existing monolithic
bundle keep working unchanged during the transition. The
deprecation timeline is announced via the release notes and
`README.md`; the meta-bundle is removed in the second release after
the split lands.

## 7. Build recipes

A **recipe** is a self-contained YAML file plus a small directory
of scripts and patches that fully describes how to build one
component of `libcvc-deps` from upstream sources. Recipes are the
source of truth for the CI pipeline that produces release
bundles, and they are also directly executable by developers
through `cvcpkg build`. The same recipe runs in both contexts,
so a bundle built locally is bit-identical (modulo timestamps and
build paths, which we normalize) to the one CI publishes.

The goals:

- Make it possible to **reproduce** any released bundle from
  source on a developer's machine, without GitHub Actions.
- Make it possible for a developer to **build the entire bundle
  set** locally for an OS the lab cares about but hosted runners
  don't cover well (e.g. an HPC login node with custom toolchains).
- Move the implicit knowledge currently encoded in
  `release.yml`'s monolithic shell steps into per-component
  artifacts that can be reviewed, patched, and version-controlled
  independently.

### 7.1 Layout

Recipes live in a top-level `recipes/` directory in this repo,
one subdirectory per component:

```
recipes/
  _common/                          # shared helpers, sourced by build.sh
    env-linux.sh
    env-macos.sh
    env-windows.ps1
    cmake-toolchain-<triplet>.cmake # optional toolchain files
  boost/
    recipe.yaml
    patches/
      0001-fix-cxx20-warning.patch
      0002-disable-broken-test.patch
    build.sh                        # POSIX (Linux + macOS)
    build.ps1                       # PowerShell (Windows)
    package.sh                      # optional: subset/install staging override
    test.sh                         # optional: smoke test against the install tree
  hdf5/
    recipe.yaml
    build.sh
    build.ps1
  grpc/
    recipe.yaml
    build.sh
    build.ps1
    patches/
      0001-msvc-static-runtime.patch
  vtk/
    recipe.yaml
    build.sh
    build.ps1
  ...
```

A recipe directory MUST contain `recipe.yaml` and at least one of
`build.sh` / `build.ps1`. All other files are optional.

### 7.2 `recipe.yaml` schema

```yaml
schema_version: 1
recipe:
  name: grpc                       # MUST match the bundle name in §3.3
  upstream_version: 1.76.0         # version produced by this recipe
  cvc_revision: 1                  # increment when this recipe changes such
                                   # that build output changes
  maintainer: "CyberPC Angel, LLC"
  homepage: https://grpc.io/
  license: Apache-2.0              # SPDX expression

source:
  # One of: tarball | git | vcpkg | brew | apt
  type: tarball
  url: https://github.com/grpc/grpc/archive/refs/tags/v1.76.0.tar.gz
  sha256: "0000000000000000000000000000000000000000000000000000000000000000"
  strip_components: 1
  # For git sources, GIT_TAG MUST be a full 40-char SHA, not a tag
  # or short hash (see user memory: git-fetchcontent-sha.md).
  # type: git
  # url: https://github.com/grpc/grpc.git
  # commit: "5d4f5c5e4c2f1...full 40-char SHA..."
  # submodules: true

patches:
  # Applied in order with `patch -p1` relative to the extracted source root.
  - patches/0001-msvc-static-runtime.patch

depends:
  # Build-time dependencies, expressed in the same vocabulary
  # as bundle dependencies (§3.2). Each one resolves to a
  # previously-built bundle whose install tree is added to the
  # CMAKE_PREFIX_PATH for this build.
  build:
    - name: abseil
      version: ">=20240722,<20260000"
    - name: openssl
      version: "^3.0"
    - name: zlib
      version: "^1.3"
  # Host tools required to be on PATH (not in the bundle graph).
  host_tools:
    - cmake
    - ninja
    - perl                          # OpenSSL build needs perl on Windows

build:
  # Maps each (platform, link) to the script that builds it.
  # Each entry is relative to the recipe directory.
  matrix:
    - platform: linux
      script: build.sh
      env:
        CFLAGS: "-O2 -fPIC"
        CXXFLAGS: "-O2 -fPIC -std=c++17"
    - platform: macos
      script: build.sh
      env:
        MACOSX_DEPLOYMENT_TARGET: "13.0"
    - platform: windows
      script: build.ps1
      vcpkg_port: grpc              # optional: when the canonical path is vcpkg
      vcpkg_features: ["codegen"]   # optional, port-specific features

package:
  # Tells the packager which subset of the install tree belongs to
  # this bundle. Mirrors `contents.files` in the bundle manifest
  # (§3.2) so the recipe is the source of truth.
  files:
    - bin/grpc_cpp_plugin*
    - bin/protoc*
    - lib/grpc*
    - lib/protobuf*
    - include/grpc/
    - include/grpcpp/
    - include/google/protobuf/
    - lib/cmake/grpc/
    - lib/cmake/protobuf/
  cmake_packages:
    - { name: Protobuf, targets: [protobuf::libprotobuf] }
    - { name: gRPC,     targets: [gRPC::grpc, gRPC::grpc++] }

test:
  # Optional smoke test the packager runs against the staged tree
  # before archiving. Non-zero exit fails the build.
  script: test.sh

abi:
  # Default ABI tag for bundles produced by this recipe; may be
  # overridden per-platform/per-toolchain by the matrix entry.
  cxx_std: 17
  cxx_runtime: "$detect"           # filled in by build.sh from $CXX
  libc: "$detect"
  crt_link: "$detect"
```

### 7.3 Build script contract

Every `build.sh` / `build.ps1` is invoked with a fixed set of
environment variables provided by the builder (CI job or local
`cvcpkg build`):

| Variable | Meaning |
|---|---|
| `CVC_RECIPE_DIR` | Absolute path to this recipe directory |
| `CVC_SOURCE_DIR` | Extracted upstream source, patches applied |
| `CVC_BUILD_DIR` | Scratch directory for out-of-tree builds |
| `CVC_INSTALL_DIR` | The bundle-private install prefix the script MUST install into |
| `CVC_DEPS_PREFIX` | Merged `CMAKE_PREFIX_PATH` of all `depends.build` resolved bundles |
| `CVC_PLATFORM` | `linux` \| `macos` \| `windows` |
| `CVC_ARCH` | `x86_64` \| `arm64` |
| `CVC_BUILD_TYPE` | `Release` \| `Debug` |
| `CVC_LINK` | `shared` \| `static` |
| `CVC_LINK_ACTUAL` | what the script will actually produce (§3.2 / D4) |
| `CVC_JOBS` | Parallelism (e.g. nproc) |

The script's only contract: populate `$CVC_INSTALL_DIR` with a
relocatable prefix layout (`bin/`, `lib/`, `include/`, `share/`,
`lib/cmake/`, etc.). The packager (§7.4) handles everything
else — subsetting, manifest generation, archiving, hashing.

Scripts MUST NOT touch anything outside `$CVC_BUILD_DIR`,
`$CVC_INSTALL_DIR`, and `$CVC_SOURCE_DIR`. The builder runs them
in a clean working directory with a sanitized environment.

### 7.4 The packager

The packager is a single Python entry point
(`cvcpkg.recipes.package_recipe`, also exposed as the CLI
`cvcpkg build <recipe>` and `cvcpkg pack <recipe>`) that:

1. Reads `recipe.yaml`, resolves `depends.build` against the
   local prefix or downloaded catalog bundles (same resolver as
   §5.5).
2. Fetches and verifies `source.url` against `source.sha256`,
   extracts into `$CVC_SOURCE_DIR`, applies `patches/`.
3. Sets up the env table from §7.3, invokes the matching
   matrix-entry script.
4. After the script returns, runs the `test.script` (if any)
   against `$CVC_INSTALL_DIR`.
5. Copies the install tree into a staging directory (the whole
   tree — `package.files` turned out to be declarative rather than
   a filter; it is verified against the staged tree, not applied to
   it), generates `share/libcvc-deps/manifest.yaml` from
   `recipe.yaml` + measured file lists + probed upstream version,
   archives the staging directory, and emits sha256 + size.

Normalizations the packager applies for reproducibility:

- Strips build-host paths from `lib/cmake/*` exported configs
  (CMake's own `--install` handles most of this; we belt-and-
  suspender it with a sed pass).
- Zeros archive entry timestamps (`tar --mtime=@0`, `7z` with a
  fixed `-mtm-` flag).
- Sorts archive entries lexicographically.

These give us identical bundle sha256s across runs of the same
recipe (modulo upstream non-determinism, which we treat as a
bug to be patched in `patches/`).

### 7.5 `cvcpkg build`

The `cvcpkg` CLI gains commands for working with recipes:

```
cvcpkg build <recipe>...   [--platform P] [--config release|debug]
                           [--link shared|static] [--from-source]
                           [--prefix DIR] [--keep-build-dir]
cvcpkg pack  <recipe>...   # build + produce a tar.gz/zip in ./dist
cvcpkg world [--from FILE] # build every recipe needed to satisfy the requirements
cvcpkg recipes [--list | --show <name> | --validate]
```

- `cvcpkg build grpc` resolves dependencies (downloading prebuilt
  bundles from the catalog if available, or recursively building
  them with `--from-source`), invokes the recipe, and installs
  the result into the active prefix.
- `cvcpkg world --from cvc-requirements.yaml` is the developer
  equivalent of the full CI pipeline: builds every recipe needed
  to satisfy the requirements, in topological dependency order,
  reusing already-built bundles when their `(recipe sha256,
  depends sha256)` matches.
- `cvcpkg recipes --validate` runs a static check on all
  recipes: schema validation, `source.sha256` reachability,
  patch applicability against a fresh source checkout, build
  script existence and shebang, and no cycles in `depends.build`.

### 7.6 Recipe sources of upstream packaging

Some components are best built via an existing package manager
(vcpkg on Windows for grpc/protobuf/openssl, Homebrew bottles on
macOS for certain tools). Recipes can declare this directly:

```yaml
source:
  type: vcpkg
  port: grpc
  triplet: x64-windows-static
  baseline: "2025.10.19"             # vcpkg-baseline pin
```

When `source.type` is `vcpkg`, the packager invokes `vcpkg
install <port>:<triplet>` against the pinned baseline, then
copies the resulting tree out of `vcpkg_installed/<triplet>/`
into `$CVC_INSTALL_DIR`. `patches:` for vcpkg ports are applied
via the port-overlay mechanism the workflow already uses today.

This matches what `release.yml` currently does in the
`windows-vcpkg` job — the recipe just makes it explicit and
per-component instead of one giant shell block.

### 7.7 CI vs developer parity

The CI `package` stage (§8) becomes a thin loop:

```bash
for recipe in recipes/*/recipe.yaml; do
    cvcpkg pack "$(dirname "$recipe")" \
        --platform $CVC_PLATFORM \
        --config   $CVC_BUILD_TYPE \
        --link     $CVC_LINK
done
```

A developer running the same `cvcpkg pack ...` command on their
laptop gets the same output (subject to toolchain identity —
the ABI tag in the resulting manifest will reflect the
developer's compiler, not the CI runner's). Recipes are the
unit of trust: review changes to a recipe in PR, and that's the
same thing CI will execute on merge.

## 8. CI changes (sketch only — no implementation in this doc)

- The existing `windows-vcpkg`, `windows-vtk`, `linux`, `macos`
  build jobs are unchanged.
- A new `package` stage replaces the current single-archive
  `Pack zip` step. It:
  1. Reads `packaging/components.yaml` (the source of truth for
     §3.3's component table).
  2. For each component, invokes `cvcpkg pack recipes/<name>`
     (§7.5), which executes the recipe's build script(s), stages
     the install tree (all of it — `package.files` declares and is
     verified against that tree rather than selecting from it), and
     archives it.
  3. Generates `share/libcvc-deps/manifest.yaml` from the
     recipe's `recipe.yaml` + measured file lists + upstream-
     version probes (`<pkg>-config.cmake`, `pkg-config --modversion`,
     etc.).
  4. Archives each component (tar.gz on Linux/macOS, zip via 7z on
     Windows) and computes SHA-256 + size.
  5. Aggregates per-component metadata into the release
     `*-index.yaml`. A separate `catalog-publish` workflow
     triggered by the release tag merges this index into a new
     append-only revision of `catalog/<rev>.yaml`, updates
     `catalog/index.yaml` and `catalog/latest.yaml`, and pushes
     to the `gh-pages` branch.
  6. **Skips re-archiving** any component whose computed
     `(recipe sha256, depends sha256, toolchain id)` matches a
     bundle already published in a previous release; the new
     release index simply references the existing artifact URL.
     This is what makes `<upstream-version>+cvc.<rev>` stable
     across releases when nothing about the component changed.
- The `release` job uploads all component archives plus the index
  to the GitHub Release.
- An optional `meta-bundle` job assembles `libcvc-deps-all-*`
  from the per-component archives for backwards compatibility
  (cheap: just `tar -A` / 7z concatenate of already-built archives
  into one).

## 9. Migration plan

| Phase | Goal | Output | Acceptance criterion | Status |
|---|---|---|---|---|
| 0 | This doc | `docs/roadmap/split-distribution.md` | Reviewed | **Done** (PR #31) |
| 1 | `packaging/components.yaml` + manifest schema | Source of truth + JSON-Schema / YAML schema doc | `make validate-components` in CI passes | **Done** (PR #32) |
| 1b | `recipes/` directory with recipe schema (§7.2) + builder contract (§7.3) | One reference recipe (`zlib`) end-to-end on Linux | `cvcpkg pack recipes/zlib` produces a valid bundle locally | **Done** (PR #32) |
| 2 | `./` Poetry package (CLI, catalog-aware resolver, downloader, cache, recipe packager) | Working `cvcpkg install` against a mocked catalog and `cvcpkg pack` against one recipe; `poetry build` produces a wheel | Unit tests pass; `pipx install ./dist/cvcpkg-*.whl` works; resolver picks correctly across two mock releases | **Done** (PR #32) |
| 2b | `cvcpkg` published to PyPI as `0.1.0a1` | `pip install cvcpkg` works on Linux/macOS/Windows | Pre-release tag `cvcpkg-v0.1.0a1` triggers `cvcpkg-publish.yml`; package visible on pypi.org | **Done** (PR #32) |
| 2c | Recipes for all components (§3.3) in `recipes/` | Every existing component has a recipe.yaml + build script(s) | `cvcpkg recipes --validate` passes; `cvcpkg world` reproduces the current Linux bundle set on a clean host | **Done** (PR #32) |
| 3 | CI package stage on Linux first | Linux component bundles + per-release index + rolling catalog alongside the existing monolithic bundle, produced via `cvcpkg pack` | `cvcpkg install --from requirements.yaml --prefix /tmp/p` succeeds against the published catalog, downstream `libcvc` builds against `/tmp/p` | **Done** (PR #32) |
| 4 | macOS + Windows package stages | Full per-platform bundle set via per-platform recipes | All three platforms produce per-component bundles in CI | **Done** (PR #33) |
| 5 | Downstream adoption | `libcvc`, `volrover3`, `TexMol`, `F2Dock`, `molsurf` switch to `cvc-requirements.yaml` | Each downstream's CI uses `cvcpkg` | **In progress** |
| 6 | Deprecate monolithic bundle | First release without `libcvc-deps-all-*` | Release notes call out the removal | Not started |

Each phase is independently shippable; phases 1–4 are pure
additions and do not change consumer behavior.

## 10. Decisions and follow-ups

Decisions made (baked into the design above):

- **D1 — Catalog hosting (was Q1).** The aggregated catalog is
  served from GitHub Pages at
  `https://transfix.github.io/libcvc-deps/catalog/`, updated by a
  workflow on every release tag. `catalog/latest.yaml` is the
  stable entry point; per-revision snapshots live at
  `catalog/<rev>.yaml`. See §3.2.
- **D2 — Append-only versioned catalog (was Q2).** Catalog
  revisions are monotonically numbered, immutable once published,
  and signed. Lockfiles pin both `catalog_revision` and
  `catalog_sha256`, so any past resolution can be replayed
  bit-identically. The publish workflow refuses to push a
  revision that mutates or removes an existing entry. See §3.2
  and §5.4.
- **D4 — `link_actual` field (was Q4).** Manifests declare both
  the consumer-facing `link` mode and a `link_actual` describing
  what the bundle ships (`shared` | `static` | `hybrid`). This
  makes Windows static bundles that stitch in DLL fallbacks for
  grpc/cgal/etc. explicit. See §3.2.
- **D5 — No separate PDB bundles (was Q5).** Debug bundles
  continue to ship PDBs inline. Consumers who do not need to
  debug into our deps should use the Release bundle.
- **D6 — ABI tag enforced by default (was Q6).** Manifests
  declare a `bundle.abi` block (`cxx_std`, `cxx_runtime`, `libc`,
  `crt_link`). The resolver enforces pairwise ABI compatibility
  by default and emits a hard error on mismatch. Users who
  knowingly accept the risk can pass `cvcpkg --ignore-abi` or
  set `accept_abi_mismatch: true` in the requirements file. See
  §3.2.1.

Follow-ups (out of scope for v1, tracked for later releases):

- **F1 — Catalog signing (was Q3, deferred).** Until cosign /
  Sigstore integration lands, catalog integrity is verified via
  the `catalog/index.yaml` sha256 list (itself fetched over
  HTTPS from GitHub Pages). A later release adds signature
  verification of `catalog/<rev>.yaml.sig` against the project's
  published public key, with the same `--ignore-signature`
  escape hatch pattern as ABI checks.

## 11. Summary

- Split the monolithic `libcvc-deps` bundle into ~25 per-component
  archives. Each bundle has its **own** version
  (`<upstream-version>+cvc.<rev>`) and declares dependencies in
  terms of other components' versions, not the `libcvc-deps`
  release version.
- Publish a per-release `*-index.yaml` (the curated, tested set
  for that release) **and** a rolling `libcvc-deps-catalog.yaml`
  aggregating every bundle from every release.
- Ship a small Python tool (`cvcpkg`) whose resolver searches the
  full catalog, so a single materialized prefix can pull bundles
  from multiple `libcvc-deps` releases as needed — supporting
  downstreams that pin old component versions, downstreams that
  want a future release's bugfix for one component only, and
  components that get dropped from future releases.
- Keep the existing monolithic bundle one extra release for
  backwards compatibility.
- Result: downstream projects download only what they need, the
  >2 GB Windows monoliths go away, and the existing
  `CMAKE_PREFIX_PATH=<prefix>` consumer story stays identical.
