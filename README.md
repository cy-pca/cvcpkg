# libcvc-deps

Prebuilt **third-party dependency bundles** for
[libcvc](https://github.com/transfix/libcvc) and the volrover3 GUI.

This repo doesn't host any library source of its own. Each tagged
release ships ready-to-use archives that contain the full set of
dependencies libcvc + volrover3 need, laid out as a single CMake
`CMAKE_PREFIX_PATH` root. Consumers point `cmake` at the extracted
directory and skip the long `apt`/`brew`/`vcpkg` install plus the
~30 min VTK-from-source build that libcvc CI does today.

## What's in the box

Every archive contains, in one tree:

**libcvc deps**

- Boost (thread, date_time, regex, filesystem, system, chrono,
  program_options, signals2, format, property_tree, …)
- HDF5 (with C++ bindings)
- FFTW3
- GSL + GSL CBLAS
- log4cplus
- libtiff (provides TIFF read/write for `libiimod::iimod`)
- libyaml 0.2.x C parser/emitter for YAML configuration files
- Protocol Buffers + gRPC C++ runtime and code-generation tools
- CGAL + GMP + MPFR
- ImageMagick (Q16-HDRI; Windows uses the [overlay port](vcpkg-overlay/ports/imagemagick))

**volrover3 deps**

- Qt 6.7.x (`QtCore`, `QtGui`, `QtWidgets`, `QtOpenGL`, `QtOpenGLWidgets`)
- VTK 9.5.0 built against Qt 6 (no QtQuick modules)
- GLEW, OpenGL, Xrender/Xcursor/Xinerama/Xi (Linux)

**F2Dock / F3Dock deps**

- NFFT3 (Linux: `libnfft3-dev`; macOS and Windows: built from upstream
  source via autotools — the same toolchain Linux's distro package and
  upstream's own Windows release use — with OpenMP on Linux/Windows.
  On Windows we additionally generate an MSVC-friendly import library
  (`gendef` + `lib /def:`) and stage the mingw runtime DLLs it needs.
  Source tarball is SHA256-pinned and fetched from the GitHub release
  with a TU-Chemnitz mirror as fallback.)
- Eigen3 (header-only, all platforms)
- LAPACK + BLAS (Linux apt LAPACK + reference BLAS; macOS brew
  `lapack`; Windows vcpkg `clapack` + `openblas`)
- levmar 2.6, built with LAPACK enabled, exported as `levmar::levmar`
- pthreads4w on Windows, so projects that use POSIX thread APIs can keep
  using CMake's `find_package(Threads)` / `Threads::Threads` flow

**Mesh / geometry deps**

- vcglib (header-only mesh processing library from the Visual
  Computing Lab of ISTI - CNR). Same release on every platform,
  fetched as a SHA256-pinned source tarball from GitHub and staged as
  both `include/vcg/` + `include/wrap/` for plain `#include` use and
  as `share/vcglib/` for `add_subdirectory()` use. A small
  `vcglib-config.cmake` is generated so `find_package(vcglib CONFIG)`
  exposes a `vcglib::vcglib` INTERFACE target that depends on the
  libcvc-deps-bundled Eigen3.
- libiimod (MRC / TIFF / image-file I/O subset of IMOD, built from
  the read-only archive [LabShare-Archive/IMOD](https://github.com/LabShare-Archive/IMOD)
  at a pinned upstream commit — see
  [`third-party/libiimod/CMakeLists.txt`](third-party/libiimod/CMakeLists.txt)).
  Shipped as a static `libiimod.a` / `iimod.lib` plus headers under
  `include/libiimod/` and a `libiimod-config.cmake` that exports
  the `libiimod::iimod` imported target. TIFF reads/writes are
  enabled — the target links transitively against the bundled
  `TIFF::TIFF`, so consumers of `libiimod::iimod` get full TIFF
  support without any extra `find_package` call. libcvc consumes
  this in place of its previous in-tree copy.

F2Dock additionally needs `cvc::xmlrpc` from libcvc itself; that is
shipped by the libcvc release artifacts, not by libcvc-deps.

All `*Config.cmake` files are placed under
`<root>/lib/cmake/<Pkg>/` (Linux/macOS) or `<root>/share/<pkg>/` /
`<root>/share/cmake/<Pkg>/` (Windows / vcpkg layout) so
`find_package(<Pkg> CONFIG)` works out of the box once the prefix is
passed in.

## Platforms / configurations

| OS      | Arch    | Build types     | Linkage         |
|---------|---------|-----------------|-----------------|
| Linux   | x86_64  | Debug, Release  | shared, static  |
| macOS   | arm64   | Debug, Release  | shared, static  |
| Windows | x86_64  | Debug, Release  | shared, static  |

Archive naming:

```
libcvc-deps-<ver>-linux-<arch>-<config>[-static].tar.gz
libcvc-deps-<ver>-macos-<arch>-<config>[-static].zip
libcvc-deps-<ver>-windows-<arch>-<config>[-static].zip
```

Examples:

- `libcvc-deps-1.1.0-linux-x86_64-release-shared.tar.gz`
- `libcvc-deps-1.1.0-windows-x86_64-debug-static.zip`

The shared archives ship `.so` / `.dylib` / `.dll` with RPATH /
install_name fixups so the loader resolves intra-archive
dependencies regardless of where you extract. The static archives
ship `.a` / `.lib`. Windows Debug archives carry MSVC `.pdb` files
for every bundled DLL.

### Compiler runtime libraries

Shared bundles also carry compiler runtime libraries so consumers
do not have to coordinate separate redistributable installs:

- **Linux**: `libstdc++.so.6` and `libgcc_s.so.1` are bundled
  alongside `libgfortran.so.5` and `libgomp.so.1`. Only glibc and
  the dynamic loader are taken from the host.
- **Windows Release**: the MSVC 2015–2022 CRT DLLs (`msvcp140*.dll`,
  `vcruntime140*.dll`, `concrt140.dll`) are bundled in `bin/`. The
  *Visual C++ Redistributable* installer is no longer required on
  consumer machines. The Universal CRT is part of Windows and is
  not bundled.
- **Windows Debug**: the debug CRT is not redistributable under
  Microsoft's license. Debug archives still require Visual Studio
  2022 or the Windows SDK debug runtime on the consumer machine.
- **macOS**: nothing extra to bundle; the OS provides `libSystem`
  and `libc++` with a forward-compatibility guarantee.

See [USAGE.md](USAGE.md#runtime-libraries-c--c--fortran-runtimes)
for details, override knobs, and rpath guidance.

## Usage

Download and extract the archive matching your operating system,
architecture, build type, and linkage preference. Then pass the
extracted directory to CMake as a prefix. The bundle is intentionally
laid out like a normal CMake package prefix: headers are under
`include/`, libraries under `lib/` / `bin/`, and package files under
`lib/cmake/`, `share/`, or `share/cmake/` depending on the upstream
project's native layout.

### Build libcvc against a release bundle

`libcvc` is the canonical consumer. A Release shared build on Linux
looks like this:

```sh
VER=1.1.0
STEM=libcvc-deps-${VER}-linux-x86_64-release-shared

curl -fLO "https://github.com/transfix/libcvc-deps/releases/download/v${VER}/${STEM}.tar.gz"
tar xzf "${STEM}.tar.gz"
export LIBCVC_DEPS_ROOT="$PWD/${STEM}"

cmake -S libcvc -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$LIBCVC_DEPS_ROOT" \
  -DCVC_BUILD_VOLROVER3=ON \
  -DCVC_ENABLE_MESHER=ON \
  -DCVC_ENABLE_SDF=ON
cmake --build build --parallel
```

On macOS and Windows, download the matching `.zip` archive and pass
the extracted directory the same way. For example, on Windows:

```powershell
$ver = "1.1.0"
$stem = "libcvc-deps-$ver-windows-x86_64-release-shared"
Invoke-WebRequest `
  -Uri "https://github.com/transfix/libcvc-deps/releases/download/v$ver/$stem.zip" `
  -OutFile "$stem.zip"
Expand-Archive "$stem.zip" -DestinationPath .

cmake -S libcvc -B build -G "Visual Studio 17 2022" -A x64 `
  -DCMAKE_PREFIX_PATH="$PWD\\$stem" `
  -DCVC_BUILD_VOLROVER3=ON `
  -DCVC_ENABLE_MESHER=ON `
  -DCVC_ENABLE_SDF=ON
cmake --build build --config Release --parallel
```

On single-config generators such as Ninja, the archive's build type
should match `CMAKE_BUILD_TYPE`. On multi-config generators such as
Visual Studio, build the configuration that matches the archive you
extracted. Mixing Debug and Release is especially important to avoid
on Windows because the MSVC runtime differs (`/MDd` vs `/MD`).

### Use the bundle from another CMake project

Downstream projects can treat `libcvc-deps` as a prebuilt science
stack. The small `libcvc-depsConfig.cmake` package is provided as a
smoke check: it proves that `CMAKE_PREFIX_PATH` points at a
libcvc-deps distribution, but individual libraries should still be
found with their normal package names.

```cmake
cmake_minimum_required(VERSION 3.24)
project(MyScienceApp LANGUAGES C CXX)

find_package(libcvc-deps CONFIG REQUIRED)

find_package(Boost 1.90 REQUIRED COMPONENTS filesystem program_options system thread)
find_package(HDF5 REQUIRED COMPONENTS C CXX)
find_package(FFTW3 CONFIG REQUIRED)
find_package(GSL REQUIRED)
find_package(LAPACK REQUIRED)
find_package(Eigen3 CONFIG REQUIRED)
find_package(levmar CONFIG REQUIRED)
find_package(log4cplus CONFIG REQUIRED)
find_package(libiimod CONFIG REQUIRED)
find_package(yaml CONFIG REQUIRED)
find_package(Protobuf CONFIG REQUIRED)
find_package(gRPC CONFIG REQUIRED)
find_package(vcglib CONFIG REQUIRED)

add_executable(my-science-app src/main.cpp)
target_link_libraries(my-science-app PRIVATE
  Boost::filesystem
  Boost::program_options
  Boost::system
  Boost::thread
  HDF5::HDF5
  FFTW3::fftw3
  GSL::gsl
  GSL::gslcblas
  LAPACK::LAPACK
  Eigen3::Eigen
  levmar::levmar
  log4cplus::log4cplus
  libiimod::iimod
  yaml
  protobuf::libprotobuf
  gRPC::grpc++
  vcglib::vcglib)
```

GUI applications can add Qt and VTK in the same prefix:

```cmake
find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets OpenGL OpenGLWidgets)
find_package(VTK 9.5 REQUIRED COMPONENTS
  CommonCore
  CommonDataModel
  FiltersCore
  GUISupportQt
  RenderingCore
  RenderingOpenGL2)

target_link_libraries(my-viewer PRIVATE
  Qt6::Core
  Qt6::Gui
  Qt6::Widgets
  Qt6::OpenGL
  Qt6::OpenGLWidgets
  ${VTK_LIBRARIES})
```

If your project consumes both a `libcvc-deps` bundle and an installed
`libcvc` package, pass both prefixes. Put `libcvc-deps` first so its
package files satisfy libcvc's transitive dependency lookups:

```sh
cmake -S app -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$LIBCVC_DEPS_ROOT;$LIBCVC_ROOT"
```

Then in CMake:

```cmake
find_package(libcvc-deps CONFIG REQUIRED)
find_package(cvc CONFIG REQUIRED)

add_executable(my-tool src/my_tool.cpp)
target_link_libraries(my-tool PRIVATE cvc::cvc)
```

### Fetch from GitHub Actions

For GitHub Actions workflows, this repository also publishes a small
fetch action. It downloads the archive for the current runner,
extracts it, and exposes the prefix path for later CMake steps:

```yaml
- name: Fetch libcvc-deps
  id: deps
  uses: transfix/libcvc-deps/.github/actions/fetch@v1.1.0
  with:
    version: "1.1.0"
    build_type: Release
    link: shared

- name: Configure
  run: |
    cmake -S . -B build -G Ninja \
      -DCMAKE_BUILD_TYPE=Release \
      -DCMAKE_PREFIX_PATH="${{ steps.deps.outputs.path }}"
```

The action is intentionally thin; plain `curl` / `tar` /
`Expand-Archive` scripts are just as valid for projects that need
custom artifact selection.

See [USAGE.md](USAGE.md) for matrix tables, version pins, and the
copy-paste snippets for `libcvc` and downstream CI.

## Releases

Tagged releases (`v1.0.0`, `v1.0.1`, `v1.0.2`, …) are produced by
GitHub Actions
([`.github/workflows/release.yml`](.github/workflows/release.yml))
and uploaded to the corresponding [GitHub
Release](https://github.com/transfix/libcvc-deps/releases). The
workflow mirrors libcvc's own release pipeline:

- Linux: Ubuntu `apt-get` packages + from-source VTK 9.5.0 (Qt6).
- macOS: Homebrew bottles for everything, including VTK and Qt 6.
- Windows: `vcpkg` for the C/C++ deps + `aqtinstall` for Qt 6 +
  from-source VTK 9.5.0.

CUDA toolkit is **not** bundled here. Consumers install CUDA on the
build host the same way libcvc CI does (`Jimver/cuda-toolkit`) — it
is small relative to the rest of the dep graph and benefits from
the toolkit installer's own setup.

## Version pins

Some components are pinned in the workflow itself (VTK, Qt on Windows,
log4cplus, NFFT, vcglib, libiimod, levmar, the Windows ImageMagick
overlay port). Others are taken as-is from each platform's package
manager and therefore drift between releases. For every tagged release
of libcvc-deps we record the **exact versions actually shipped** in the
artifacts below. Consumers who need bit-for-bit reproducible bundles
should consume a tagged release rather than the moving tip.

A given release of libcvc-deps is intended to be used with the
corresponding (or older) libcvc release. Bumping a major version of
Qt or VTK is reflected by bumping libcvc-deps's own version.

### v1.1.0 (2026-05-19)

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

New / changed pins in v1.1.0:

| Component | Linux | macOS | Windows |
|---|---|---|---|
| levmar | 2.6 (vendored source) | 2.6 (vendored source) | 2.6 (vendored source) |
| libyaml | 0.2.5 (`libyaml-dev`) | 0.2.5 (Homebrew `libyaml`) | 0.2.5 (vcpkg `libyaml`) |
| Protobuf | 3.21.12 (`libprotobuf-dev`) | 34.1 (Homebrew `protobuf`) | 6.33.4 (vcpkg `protobuf`) |
| gRPC | 1.51.1 (`libgrpc++-dev`) | 1.80.0 (Homebrew `grpc`) | 1.76.0 (vcpkg `grpc`) |
| Abseil | 20220623.1 (`libabsl-dev`) | 20260107.1 (Homebrew `abseil`) | 20260107.1 (vcpkg `abseil`) |
| c-ares | 1.27.0 (`libc-ares-dev`) | 1.34.6 (Homebrew `c-ares`) | 1.34.6 (vcpkg `c-ares`) |
| OpenSSL | 3.0.13 (`libssl-dev`) | 3.6.2 (Homebrew `openssl@3`) | 3.6.2 (vcpkg `openssl`) |
| RE2 | 20230301 (`libre2-dev`) | 2025-11-05 (Homebrew `re2`) | 2025-11-05 (vcpkg `re2`) |
| zlib | 1.3 (`zlib1g-dev`) | macOS SDK / Homebrew dependency | 1.3.2 (vcpkg `zlib`) |
| pthreads4w | n/a | n/a | vcpkg `pthreads` |

Pin-source notes for v1.1.0:

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

### v1.0.2 (2026-05-14)

Point release fixing the macOS volrover3 bundle and tightening the
Linux Qt6 layout discovered while exercising the v1.0.1 artifacts in
libcvc's release pipeline. No upstream component versions changed
from v1.0.1; the package manifest is identical.

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

### v1.0.1 (2026-05-13)

First point release. Same upstream component manifest as v1.0.0;
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

### v1.0.0 (2026-05-13)

Versions actually shipped in the `v1.0.0` release artifacts. Where a
platform's package manager picked a different upstream version than
the workflow's nominal pin (e.g. Homebrew's VTK bottle), the shipped
version is recorded here verbatim.

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

Pin-source notes:

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

## License

This repository's own files (the workflow, the CMake glue, the
README/USAGE docs) are licensed under the GNU General Public
License version 2 (see [`LICENSE`](LICENSE)).

The archived release artifacts are derivative bundles of upstream
projects. Each upstream component retains its own license; the
release notes link to the relevant license texts.
