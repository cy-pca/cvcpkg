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
- CGAL + GMP + MPFR
- ImageMagick (Q16-HDRI; Windows uses the [overlay port](vcpkg-overlay/ports/imagemagick))

**volrover3 deps**

- Qt 6.7.x (`QtCore`, `QtGui`, `QtWidgets`, `QtOpenGL`, `QtOpenGLWidgets`)
- VTK 9.5.0 built against Qt 6 (no QtQuick modules)
- GLEW, OpenGL, Xrender/Xcursor/Xinerama/Xi (Linux)

**F2Dock / F3Dock deps**

- NFFT3 (Linux: `libnfft3-dev`; macOS: brew `nfft`; Windows: built
  from upstream source in MSYS2/mingw64 with OpenMP and AVX2 — the
  same toolchain upstream uses for its official Windows release — and
  staged with an MSVC-friendly import library (`gendef` + `lib /def:`)
  alongside the mingw runtime DLLs it needs, plus a rewritten
  `nfft3.pc` for `pkg_check_modules` consumers. Source tarball is
  pinned by SHA256 and fetched from the GitHub release with a
  TU-Chemnitz mirror as fallback.)
- Eigen3 (header-only, all platforms)
- LAPACK + BLAS (Linux apt LAPACK + reference BLAS; macOS brew
  `lapack`; Windows vcpkg `clapack` + `openblas`)

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

- Boost: latest in Ubuntu LTS / vcpkg / Homebrew (tracked, not pinned)
- HDF5: distro / vcpkg / brew
- FFTW3: distro / vcpkg / brew
- GSL: distro / vcpkg / brew
- log4cplus: distro / vcpkg / brew
- CGAL / GMP / MPFR: distro / vcpkg / brew
- ImageMagick: 7.1.2-21 Q16-HDRI x64 (Windows overlay)
- **Qt: 6.7.x** (`install-qt-action` `6.7.*` on Windows; `qt@6` / `qt6-base-dev`
  on macOS / Linux)
- **VTK: 9.5.0** built against Qt 6 (no `GUISupportQtQuick` / `RenderingQtQuick`)
- NFFT3: 3.5.3 (Linux apt `libnfft3-dev` / brew `nfft` /
  built from source in MSYS2 mingw64 on Windows,
  SHA256 `caf1b3b3e5bf8c33a6bfd7eca811d954efce896605ecfd0144d47d0bebdf4371`)
- Eigen3: 3.4.x distro / vcpkg / brew
- LAPACK / BLAS: distro reference impl / brew `lapack` /
  vcpkg `clapack` + `openblas`

A given release of libcvc-deps is intended to be used with the
corresponding (or older) libcvc release. Bumping a major version
of Qt or VTK is reflected by bumping libcvc-deps's own version.

## License

This repository's own files (the workflow, the CMake glue, the
README/USAGE docs) are MIT-licensed (see [`LICENSE`](LICENSE)).

The archived release artifacts are derivative bundles of upstream
projects. Each upstream component retains its own license; the
release notes link to the relevant license texts.
