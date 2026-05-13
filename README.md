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

- `libcvc-deps-1.0.0-linux-x86_64-release-shared.tar.gz`
- `libcvc-deps-1.0.0-windows-x86_64-debug-static.zip`

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

Download and extract the archive matching your toolchain, then pass
the extracted root to CMake as a prefix:

```sh
# Linux example
tar xzf libcvc-deps-1.0.0-linux-x86_64-release-shared.tar.gz
export DEPS=$PWD/libcvc-deps-1.0.0-linux-x86_64-release-shared

# Now build libcvc against the bundled deps
cmake -S libcvc -B build -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH="$DEPS" \
  -DCVC_BUILD_VOLROVER3=ON \
  -DCVC_ENABLE_MESHER=ON \
  -DCVC_ENABLE_SDF=ON
cmake --build build --parallel
```

A tiny `libcvc-depsConfig.cmake` is included so that
`find_package(libcvc-deps CONFIG REQUIRED)` succeeds against the
extracted tree — handy for downstream projects to assert that they
are configured against a known dep distribution.

See [USAGE.md](USAGE.md) for matrix tables, version pins, and the
copy-paste snippets for `libcvc` and downstream CI.

## Releases

Tagged releases (`v1.0.0`, …) are produced by GitHub Actions
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
log4cplus, NFFT, vcglib, libiimod, the Windows ImageMagick overlay
port). Others are taken as-is from each platform's package manager and
therefore drift between releases. For every tagged release of
libcvc-deps we record the **exact versions actually shipped** in the
artifacts below. Consumers who need bit-for-bit reproducible bundles
should consume a tagged release rather than the moving tip.

A given release of libcvc-deps is intended to be used with the
corresponding (or older) libcvc release. Bumping a major version of
Qt or VTK is reflected by bumping libcvc-deps's own version.

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
