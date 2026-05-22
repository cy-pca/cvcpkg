# Roadmap: new dependency bundles

Status: proposal (not yet implemented).
Author: drafted 2026-05-21.

This document captures the next batch of components we want to add
to `libcvc-deps`. None of this work should start until the
[split-distribution roadmap](split-distribution.md) is implemented
and stable, for two reasons:

1. Every component we add today goes into the monolithic bundle,
   pushing the Windows artifacts further over the 2 GB envelope
   that already required the LZMA2 7z workaround. Adding the
   Emscripten SDK, a second full Qt 6 build for WASM, and SWIG
   would add multiple GB of payload that almost no consumer needs.
2. The dependencies below are intrinsically optional and consumed
   by a small subset of downstreams (WASM builds, language-binding
   work). They are textbook candidates for the per-component
   bundle layout proposed in the split-distribution roadmap, where
   each lands as its own archive with its own manifest and is
   pulled in only when a consumer asks for it.

Sequencing therefore is:

> **Land the split distribution first. Then add the components
> below as separate per-component bundles. Do not add them to the
> monolithic bundle.**

The plan below assumes the split-distribution work has finished and
that each new component ships as its own bundle keyed on
`(component, version, platform, arch, build_type, link)` per the
split-distribution catalog.

## Index

- [1. Emscripten SDK (WASM toolchain)](#1-emscripten-sdk-wasm-toolchain)
- [2. SWIG (Python and C# wrapper generator)](#2-swig-python-and-c-wrapper-generator)
- [3. Qt 6 `wasm_singlethread` build](#3-qt-6-wasm_singlethread-build)
- [4. Zstd (fast compression library)](#4-zstd-fast-compression-library)
- [5. Sequencing and integration notes](#5-sequencing-and-integration-notes)

---

## 1. Emscripten SDK (WASM toolchain)

### Why

A WASM build target for libcvc / volrover3 is on the medium-term
roadmap. The Emscripten SDK provides `emcc` / `em++` (the Clang-
based C/C++ → wasm compiler), `emcmake` / `emmake` (CMake +
autotools wrappers), `node` (used at link time for the JS shim),
and the matching `libc++` / sysroot. None of this is available
from the host package managers we use today in shape or version
parity.

### What we ship

A single per-platform bundle whose layout matches an installed
`emsdk` tree:

```
emsdk-<ver>-<platform>-<arch>/
├── emsdk/                          # the emsdk metadata + activator
├── upstream/                       # LLVM/Clang toolchain (emscripten-llvm)
│   ├── bin/{emcc, em++, llvm-*, clang*, …}
│   ├── lib/
│   └── emscripten/                 # python + JS shims (emcc.py, …)
├── node/                           # bundled Node.js used by emcc at link time
└── bin/                            # convenience PATH stub
```

The bundle is *standalone*: extracting it and sourcing the
generated `emsdk_env.sh` / `emsdk_env.ps1` is sufficient to build
WASM artifacts against the rest of libcvc-deps. We do **not** rely
on `emsdk install` at consumer time — that is a network-fetch
script and defeats the point of an offline-capable dep bundle.

### Versioning

Pin to a specific upstream emsdk release (e.g. `4.0.x`) per
libcvc-deps catalog entry. The emsdk version is independent of any
Qt / VTK pin because WASM consumers also need [§3](#3-qt-6-wasm_singlethread-build).

### Build approach

The cleanest path is to run `./emsdk install <ver> && ./emsdk
activate <ver>` inside a CI job that then **tars the resulting
directory tree verbatim**. emsdk is fundamentally a release
bundler itself; we are just snapshotting the activated state. No
from-source LLVM build is needed.

CI cost: ~2-3 GB download per platform, one-time per emsdk
version. Cache the activated emsdk prefix keyed on
`emsdk-<ver>-<platform>` so subsequent runs are zero-cost.

### Platforms

- Linux x86_64
- macOS arm64
- Windows x86_64

All three are supported upstream and all three have non-trivial
demand from our downstreams. No platform exclusions in v1 of this
bundle.

### Catalog entry (per split-distribution schema)

```yaml
name: emsdk
version: "<upstream>+cvc<rev>"
provides:
  - emsdk
runtime_requires: []       # standalone toolchain, not a libcvc-deps consumer
build_type_independent: true  # toolchain doesn't have Debug/Release flavors
link_mode: n/a
size_uncompressed_mb: ~3500   # rough, platform-dependent
```

### Open questions

- Whether to ship the Emscripten "ports" cache (precompiled libc++,
  zlib, SDL, etc.) alongside or let consumers populate it on first
  use. Bundling the ports cache makes the bundle ~500 MB larger
  but turns first-build into an offline operation. **Recommendation:
  bundle the ports cache**; partial bundles defeat the purpose.
- Whether to also bundle a pinned `node` (we do today via emsdk's
  own bundling, so probably yes — keep emsdk's bundled node).

---

## 2. SWIG (Python and C# wrapper generator)

### Why

Future libcvc consumer work includes:

- A Python wrapper for the volume / mesh primitives so that the
  data-science / notebook half of the lab can use libcvc kernels
  directly.
- A C# wrapper to drive volrover3 datasets from external Unity /
  WPF tooling.

SWIG remains the most appropriate tool for both, given the existing
C++ surface area of libcvc, the heterogeneity of target languages
(Python *and* C# from the same `.i` files), and SWIG's mature
template / shared-pointer / std::string support. The alternatives
(`pybind11`, `cppyy`, `CppSharp`) each cover one language well but
none covers both with the same workflow.

We treat SWIG strictly as a **build-time host tool**, not a
runtime dependency. The wrapper code SWIG emits links against
Python or .NET only on the consumer side; no Python / .NET
runtime is bundled here.

### What we ship

```
swig-<ver>-<platform>-<arch>/
├── bin/swig          # the swig executable (swig.exe on Windows)
├── share/swig/<ver>/ # SWIG runtime headers + language-specific
│   ├── python/       # python.swg, pyrun.swg, std_*.i, …
│   ├── csharp/       # csharp.swg, std_*.i, …
│   └── …             # other language packs come "for free"
└── lib/              # any platform-specific runtime helpers
```

Layout matches a stock `make install` from upstream SWIG. The
binary is statically linked against PCRE2 where possible so the
host doesn't need to provide it.

### Versioning

Pin to a specific upstream SWIG release (e.g. `4.3.x`). Bump in
lockstep with consumer wrapper work; SWIG releases are infrequent
and ABI-compatible across patch versions in practice.

### Build approach

SWIG is small and builds in minutes from upstream tarball via
`./configure && make`. The only non-trivial dep is PCRE2; bundle
PCRE2 statically into `swig` itself rather than carrying a
PCRE2 component bundle. Bundle source: GitHub release tarball,
SHA256-pinned.

### Platforms

- Linux x86_64: native build, GCC.
- macOS arm64: native build, Apple Clang.
- Windows x86_64: build via MSVC; SWIG upstream supports this and
  ships a prebuilt `swigwin-<ver>.zip` we can adopt directly (with
  SHA256 pin) rather than rebuilding.

### Catalog entry

```yaml
name: swig
version: "<upstream>+cvc<rev>"
provides:
  - swig
runtime_requires: []           # build-time host tool only
build_type_independent: true
link_mode: n/a
size_uncompressed_mb: ~30      # tiny by libcvc-deps standards
```

### Open questions

- Whether to expose SWIG via a `find_package(SWIG)` shim
  (`SWIGConfig.cmake`) we generate ourselves on top of the
  upstream install (upstream's `UseSWIG.cmake` is shipped by
  CMake, but `find_package(SWIG)` expects to find the binary on
  PATH). **Recommendation:** ship a tiny generated `lib/cmake/SWIG/
  SWIGConfig.cmake` that sets `SWIG_EXECUTABLE` /
  `SWIG_VERSION` / `SWIG_DIR` so CMake-side discovery is reliable
  inside the bundle.
- Whether we need a separate "swig-csharp" / "swig-python"
  component for transitive runtime files. **Recommendation:** no
  — SWIG's runtime files are tiny and shipping a single `swig`
  bundle with all language packs is clean and consistent with
  upstream's distribution model.

---

## 3. Qt 6 `wasm_singlethread` build

### Why

A WASM build of volrover3 (or any Qt-Widgets-based libcvc UI)
requires Qt itself to be built for the `wasm_singlethread` target
— Qt does **not** support runtime selection between native and
WASM ABIs from a single install tree. The native Qt 6 bundles we
ship today (`linux-g++`, `clang-darwin-arm64`, `win64_msvc2019_64`)
cannot link a WASM application.

Upstream provides this as a separate Qt-side build target. We are
explicitly choosing the **single-threaded** variant rather than the
`wasm_multithread` variant because:

- `wasm_multithread` requires SharedArrayBuffer, which in turn
  requires the consuming page to be served with `Cross-Origin-Opener-
  Policy: same-origin` + `Cross-Origin-Embedder-Policy: require-corp`
  headers. Many consumer hosting environments cannot guarantee
  this.
- `wasm_singlethread` runs on any modern browser without site-
  isolation headers and matches what the volrover3 WASM exploration
  has been targeting in its design notes.

If a future consumer needs the multithread variant we can add a
second bundle (`qt6-wasm-multithread`) without disturbing the
single-threaded one.

### What we ship

A bundle whose layout mirrors the existing Qt 6 native bundles but
keyed for the WASM target:

```
qt6-wasm-singlethread-<qtver>-<platform>-<arch>/
├── bin/                    # host-side tools (qmake6, moc, rcc, uic)
│                           # — these are NATIVE binaries, not WASM
├── include/QtCore, QtGui, QtWidgets, …
├── lib/
│   ├── *.a                 # Qt WASM libraries (static; Qt-on-WASM
│   │                       #   does not support shared libs)
│   └── cmake/Qt6*/         # CMake config files generated for the
│                           #   WASM target (consumer find_package(Qt6))
├── mkspecs/wasm-emscripten/
└── plugins/                # WASM plugins (platforms, imageformats)
```

Notes:

- **Static-only**. Qt-on-WASM upstream ships only static `.a`
  libraries. There is no `-shared` flavor of this bundle.
- The host-side `bin/` tools (`moc`, `rcc`, `uic`, `qmake6`) are
  *native* binaries for the build platform. A Qt 6 WASM build still
  needs a native host Qt to run its own code-generation tools.
  Consumers either supply a native Qt 6 from libcvc-deps's existing
  bundles, or we ship the host tools inside this bundle as a
  convenience.

  **Recommendation:** ship the host tools inside this bundle so a
  consumer doesn't need to coordinate two Qt 6 bundles at matching
  versions. The duplication is small (~30 MB).

### Versioning

Pin to the same Qt 6 minor we ship for native (currently 6.7.x).
The Qt 6 WASM port is most reliable when the native and WASM
versions match, because consumers using both inside the same
project pick up matching Qt headers.

### Build approach

The official upstream path is:

1. Stand up a **native** Qt 6 build directory on the build host
   (Qt provides this — the WASM build needs it as a "host Qt").
2. Configure Qt 6 from source with
   `-platform <host> -xplatform wasm-emscripten -no-feature-thread`
   (the `-no-feature-thread` is what selects the singlethread
   variant) plus our usual feature subset
   (Core / Gui / Widgets / OpenGL / OpenGLWidgets, no QtQuick).
3. Build with the Emscripten SDK from §1 on PATH.

This means **§1 (Emscripten SDK) is a hard prerequisite** of this
bundle's CI build. It is *not* a runtime dependency of consumers —
once Qt is built, the resulting `.a` files are consumed by the
consumer's own Emscripten toolchain, not by ours.

### Platforms

The Qt-on-WASM artifacts themselves are platform-independent (they
are wasm). However the host-side tools are not, so we still ship
three flavors:

- `qt6-wasm-singlethread-<qtver>-linux-x86_64`   (host = Linux)
- `qt6-wasm-singlethread-<qtver>-macos-arm64`    (host = macOS)
- `qt6-wasm-singlethread-<qtver>-windows-x86_64` (host = Windows)

A consumer building WASM from a Linux host downloads the linux
flavor; the WASM libs inside are identical across all three.

### Catalog entry

```yaml
name: qt6-wasm-singlethread
version: "<qt-upstream>+cvc<rev>"
provides:
  - qt6-wasm-singlethread
runtime_requires: []           # static-link; no runtime deps from
                               # other libcvc-deps components
build_requires:
  - emsdk                      # the Emscripten SDK (CI build only)
build_type_independent: false  # ship Release; Debug WASM artifacts
                               # are huge and rarely useful
link_mode: static
size_uncompressed_mb: ~1200    # rough, dominated by libQt6Core.a +
                               # libQt6Gui.a + libQt6Widgets.a
```

### Open questions

- Whether to bundle a Debug flavor at all. WASM debug builds are
  enormous (5-10x release size) and source maps + DWARF emit
  separately anyway. **Recommendation:** Release-only for v1.
- Whether to enable QtSvg in the WASM build. Native bundles ship
  it; volrover3's WASM target may or may not need it. Decide
  during integration with the actual volrover3 WASM work.
- Whether to verify the bundle with a minimal "hello, world" Qt
  WASM build in CI before publishing each release. **Strongly
  recommended** — Qt-on-WASM builds are fragile across upstream
  point releases.

---

## 4. Zstd (fast compression library)

### Why

Several libcvc consumers and dependencies already handle compressed
data (HDF5 filters, archive I/O, volume datasets). Today the
project relies on zlib for general-purpose compression, but Zstd
(Zstandard) offers significantly better compression ratios at
comparable speed, and dramatically faster decompression — typically
3-5× faster than zlib at equivalent ratios. Concrete motivations:

- **HDF5 filter integration.** HDF5 supports Zstd as a
  third-party filter via `hdf5-zstd-filter`. Volume datasets
  stored in HDF5 (a core libcvc workflow) would benefit from
  smaller files and faster reads.
- **Archive format support.** `cvcpkg` already handles `.tar.zst`
  archives (via Python's stdlib `tarfile` + the `zstandard`
  module). Shipping a native Zstd library lets C/C++ code in
  libcvc and volrover3 decompress the same archives without a
  Python dependency.
- **Upstream adoption.** Zstd has become a de-facto standard in
  systems software (Linux kernel, systemd, LLVM, Blosc2, Apache
  Arrow). Adding it now aligns future work with the ecosystem.

### What we ship

```
zstd-<ver>-<platform>-<arch>/
├── bin/zstd                    # CLI tool (zstd.exe on Windows)
├── include/
│   ├── zstd.h
│   ├── zstd_errors.h
│   └── zdict.h
├── lib/
│   ├── libzstd.{a,so,dylib}   # static and/or shared per link mode
│   └── cmake/zstd/             # upstream CMake config files
│       ├── zstdConfig.cmake
│       ├── zstdConfigVersion.cmake
│       └── zstdTargets*.cmake
└── lib/pkgconfig/libzstd.pc
```

Layout matches a stock `cmake --install` from upstream. Consumers
discover via `find_package(zstd CONFIG)` and link
`zstd::libzstd_static` or `zstd::libzstd_shared`.

### Versioning

Pin to a specific upstream release (e.g. `1.5.x`). Zstd maintains
strong backward-compatibility guarantees on both the frame format
and the library ABI; version bumps are low-risk.

### Build approach

Zstd builds in under a minute from source via CMake:

```bash
cmake -S build/cmake -B _build \
  -DCMAKE_INSTALL_PREFIX="$CVC_INSTALL_DIR" \
  -DZSTD_BUILD_PROGRAMS=ON \
  -DZSTD_BUILD_TESTS=OFF \
  -DZSTD_MULTITHREAD_SUPPORT=ON
cmake --build _build --config Release
cmake --install _build --config Release
```

No external dependencies beyond a C compiler. The recipe is
self-contained and trivial — comparable to zlib in complexity.

### Platforms

- Linux x86_64
- macOS arm64
- Windows x86_64 (MSVC)

All three are tier-1 upstream. No platform exclusions.

### Catalog entry

```yaml
name: zstd
version: "<upstream>+cvc<rev>"
provides:
  - zstd
runtime_requires: []
build_type_independent: false
link_mode: shared              # ship both; default shared
size_uncompressed_mb: ~15      # tiny
```

### Open questions

- Whether to also ship the `zstd` CLI tool (`ZSTD_BUILD_PROGRAMS=ON`)
  or only the library. The CLI is useful for ad-hoc compression in
  CI scripts and adds <1 MB. **Recommendation: ship it.**
- Whether to enable the dictionary builder (`ZSTD_BUILD_DICTBUILDER`).
  Useful if volume datasets adopt dictionary-aided compression.
  Low cost, so **recommendation: enable**.
- Whether to register a Zstd HDF5 filter plugin as part of this
  component or as a separate `hdf5-zstd-filter` bundle.
  **Recommendation:** separate bundle — the filter plugin depends
  on both HDF5 and Zstd, and belongs as its own recipe with
  `build_requires: [hdf5, zstd]`.

---

## 5. Sequencing and integration notes

The four components above interact:

```
emsdk  ─────────────► qt6-wasm-singlethread  (build-time dep)
swig                                          (independent)
zstd                                          (independent)
```

Order in which to land them post-split-distribution:

1. **SWIG first.** Smallest payload (~30 MB), independent of the
   other two, and unblocks Python / C# wrapper work that is
   already in early scoping.
2. **Zstd second.** Tiny (~15 MB), zero dependencies, trivial
   recipe. Unblocks HDF5 Zstd filter work and native archive
   decompression in libcvc.
3. **emsdk third.** Standalone toolchain; large but mechanical
   to ship. Validates that the split-distribution catalog handles
   large per-component bundles cleanly.
4. **qt6-wasm-singlethread fourth.** Needs emsdk on the CI builder
   to even produce the artifact; once emsdk is in catalog the CI
   recipe just declares `build_requires: [emsdk]`.

Re-iterating the framing in this document's preamble: **none of
this should start until the [split-distribution
roadmap](split-distribution.md) is complete.** Adding these to the
existing monolithic bundles would push the Windows artifacts well
past their current 2 GB envelope and would deliver multi-GB
payload to consumers who don't need it.

When the split-distribution catalog is operational, each of these
becomes a small, isolated addition: a new catalog entry, a new CI
job per platform, and a smoke-test consumer.
