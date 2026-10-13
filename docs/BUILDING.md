# Building from source

This document lists every tool and system dependency needed to
reproduce a full `cvcpkg build-all` run on each supported platform.
The cvcpkg remote builders install exactly these prerequisites, so
the lists below always match what the build fleet uses.

## Quick start

```sh
# Install cvcpkg (from the repo root)
pip install .

# Build everything for the current platform
cvcpkg build-all \
  --platform linux \
  --config release \
  --link shared \
  --prefix ./prefix \
  --recipes-dir recipes
```

Replace `linux` with `macos` or `windows` as appropriate.
On Windows, run from a **Developer PowerShell for VS 2022** (or
after sourcing `vcvarsall.bat x64`) so that `cl.exe`, `nmake`, and
the Windows SDK are on PATH.

---

## Linux (Ubuntu 24.04 / Debian 12+)

### Compiler toolchain

| Tool | Version | Package | Notes |
|------|---------|---------|-------|
| GCC (C/C++) | ≥ 13 | `build-essential` | Provides `gcc`, `g++`, `make` |
| GFortran | ≥ 13 | `gfortran` | Required by OpenBLAS |

### Build systems

| Tool | Version | Package | Notes |
|------|---------|---------|-------|
| CMake | ≥ 3.16 | `cmake` | 3.28 ships with Ubuntu 24.04 |
| Ninja | ≥ 1.10 | `ninja-build` | Used by all CMake recipes |
| GNU Make | ≥ 4.0 | `build-essential` | Used by OpenSSL, NFFT3 |
| Autotools | — | `autoconf automake libtool` | Used by NFFT3 |

### Interpreters

| Tool | Version | Package | Notes |
|------|---------|---------|-------|
| Python 3 | ≥ 3.10 | `python3 python3-pip python3-venv` | Runs cvcpkg |
| Perl | ≥ 5.30 | `perl` | OpenSSL Configure, Qt6 build |

### Utilities

| Tool | Package | Notes |
|------|---------|-------|
| pkg-config | `pkg-config` | Used by several CMake find modules |
| patchelf | `patchelf` | Post-build RPATH patching (shared builds) |

### System development libraries

These headers/libraries must be installed so Qt6, VTK, and
ImageMagick can build with OpenGL and X11 support:

```
libgl1-mesa-dev libxt-dev mesa-common-dev libglew-dev
libxrender-dev libxcursor-dev libxinerama-dev libxi-dev
libxext-dev libxfixes-dev libxrandr-dev libxcb1-dev
libx11-dev libx11-xcb-dev libfontconfig1-dev
libfreetype-dev libharfbuzz-dev
```

### One-liner install

```sh
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
  build-essential cmake ninja-build patchelf pkg-config \
  autoconf automake libtool gfortran perl \
  python3 python3-pip python3-venv \
  libgl1-mesa-dev libxt-dev mesa-common-dev libglew-dev \
  libxrender-dev libxcursor-dev libxinerama-dev libxi-dev \
  libxext-dev libxfixes-dev libxrandr-dev libxcb1-dev \
  libx11-dev libx11-xcb-dev libfontconfig1-dev \
  libfreetype-dev libharfbuzz-dev
```

---

## macOS (14 Sonoma / 15 Sequoia)

### Compiler toolchain

Xcode Command Line Tools (provides `clang`, `clang++`, system
headers, and the Accelerate framework for BLAS/LAPACK):

```sh
xcode-select --install
```

### Build systems and utilities (from cvcpkg)

Everything comes from cvcpkg: no Homebrew, no pip-installed cmake, no
system copies.

| Tool | cvcpkg component | Notes |
|------|------------------|-------|
| CMake | `cmake` (3.31.7) | Pinned < 4.x for Boost compat |
| Ninja | `ninja` | CMake generator |
| pkg-config | `pkg-config` (pkgconf) | Find modules, configure scripts |
| Meson | `meson` | Runs under the `python3` on PATH |
| m4, flex, bison | `m4`, `flex`, `bison` | Parser generators (postgresql-*, libpq, gstreamer) |
| Autotools | `autoconf`, `automake`, `libtool` | Not on PATH: the recipes that need them (`nfft3`, `swig`) declare them in `depends.build`, so `cvcpkg install-deps` puts them in the prefix |

Install them into a tools prefix, then put **only the tools** on PATH,
the way `macos-build.yml` and `macos-recipe-check.yml` do. CMake
searches its own install prefix for packages, and that prefix also holds
the curl/openssl headers and libraries cvcpkg installed with cmake, which
a recipe build would otherwise pick up instead of its declared deps.
`bin/cmake` loads libcurl, libssl and libcrypto from `../lib`, so those
three dylibs come along under their versioned names (which
`find_library` never matches), and bison is told where its data and m4
are:

```sh
cvcpkg install cmake ninja pkg-config meson m4 flex bison \
  --prefix ~/cvcpkg-tools-prefix --config release --link shared
p=~/cvcpkg-tools-prefix t=~/cvcpkg-tools
mkdir -p "$t/bin" "$t/lib" "$t/share"
for exe in cmake ctest cpack ninja pkgconf meson m4 flex bison; do
  cp "$p/bin/$exe" "$t/bin/"
done
ln -s pkgconf "$t/bin/pkg-config"
cp -R "$p"/share/cmake-* "$p/share/bison" "$t/share/"
cp -R "$p/lib/meson" "$t/lib/"
cp "$p/lib/libcurl.4.dylib" "$p/lib/libssl.3.dylib" "$p/lib/libcrypto.3.dylib" "$t/lib/"
export PATH="$t/bin:$PATH" M4="$t/bin/m4" BISON_PKGDATADIR="$t/share/bison"
```

If Homebrew is installed, take it off PATH for the build (CI strips
`/opt/homebrew` and, on Intel, `/usr/local`): its cmake, pkgconf,
autotools and libraries are otherwise found instead of cvcpkg's.

### Interpreters

| Tool | Notes |
|------|-------|
| Python 3 | Runs cvcpkg itself, and meson |
| Perl | Ships with macOS; `openssl`, `qt6`, `autoconf` and `automake` declare cvcpkg's `perl` in `depends.build` |

### System frameworks

macOS provides OpenGL, Accelerate (BLAS/LAPACK), and Core Text
(font rendering) via built-in frameworks — no extra packages
needed.

---

## Windows (Windows 10/11, x86_64)

### Compiler toolchain

**Visual Studio 2022** (Community, Professional, or Enterprise)
with the "Desktop development with C++" workload. This provides:

- MSVC compiler (`cl.exe`) — version 19.40+
- Windows SDK (10.0.22621 or later)
- `nmake` (used by OpenSSL)
- MSBuild

All build scripts must run from a **Developer PowerShell** or
after sourcing `vcvarsall.bat x64` so that the MSVC tools are on
PATH.

### Build systems, assembler and Perl (from cvcpkg)

Everything comes from cvcpkg: no Chocolatey, no vcpkg, no system copies.

| Tool | cvcpkg component | Notes |
|------|------------------|-------|
| CMake | `cmake` (3.31.7) | Pinned < 4.x for Boost compat |
| Ninja | `ninja` | CMake generator |
| NASM | `nasm` | OpenSSL and libjpeg-turbo use NASM for optimised x86_64 assembly. Without it OpenSSL falls back to unoptimised C (`no-asm`) and libjpeg-turbo disables SIMD. |
| Perl | `perl` | OpenSSL Configure |
| nmake | Visual Studio | OpenSSL build |

Install them into a tools prefix, then put **only the tools** on PATH,
the way `windows-build.yml` and `windows-recipe-check.yml` do. CMake
searches its own install prefix for packages, and that prefix also holds
the curl/openssl headers and libraries cvcpkg installed with cmake, which
a recipe build would otherwise pick up instead of its declared deps:

```powershell
cvcpkg install cmake ninja nasm --prefix C:\cvcpkg-tools-prefix --config release --link shared
New-Item -ItemType Directory -Force C:\cvcpkg-tools\bin, C:\cvcpkg-tools\share | Out-Null
'cmake','ctest','cpack','cmcldeps','ninja','nasm','ndisasm' | ForEach-Object {
  Copy-Item "C:\cvcpkg-tools-prefix\bin\$_.exe" C:\cvcpkg-tools\bin\
}
Copy-Item -Recurse C:\cvcpkg-tools-prefix\share\cmake-* C:\cvcpkg-tools\share\
$env:PATH = "C:\cvcpkg-tools\bin;$env:PATH"
```

`perl` can go into its own prefix the same way, with its `bin` on PATH.

### Interpreters

| Tool | Install | Notes |
|------|---------|-------|
| Python 3 | python.org installer | Runs cvcpkg itself (`pip install` of this repo) |
| PowerShell 7 | `cvcpkg install powershell` | Runs the `.ps1` recipe scripts |

### Qt6 (pre-built)

On Windows, Qt6 is installed via `jurplel/install-qt-action` in CI
(version `6.7.*`, arch `win64_msvc2019_64`). For local builds,
install Qt 6.7.x from the [Qt online
installer](https://www.qt.io/download-qt-installer-oss) and set
`Qt6_DIR` to point at the MSVC kit, e.g.:

```powershell
$env:Qt6_DIR = "C:\Qt\6.7.3\msvc2019_64\lib\cmake\Qt6"
```

### Getting started

Open a **Developer PowerShell for VS 2022** to get `cl.exe`
and `nmake` on PATH, then install the tools from cvcpkg as shown above.

---

## Tool-to-recipe mapping

Which recipes require which non-standard tools beyond the baseline
(CMake + Ninja + C/C++ compiler):

| Tool | Recipes that require it |
|------|------------------------|
| GFortran | `openblas` (Linux only) |
| Perl | `openssl`, `qt6` |
| NASM | `openssl` (Windows), `libjpeg-turbo` (Windows — SIMD) |
| Autotools | `nfft3` |
| nmake | `openssl` (Windows) |
| pkg-config | `nfft3`, various `find_package` module-mode lookups |
| patchelf | Post-build RPATH fixup (Linux shared only) |
| OpenMP runtime (`openmp`) | None on macOS: Apple clang ships no libomp, so `openblas` and `nfft3` build without OpenMP there, and `fftw3` threads with pthreads |

## Environment variables

The `cvcpkg` builder sets these environment variables before
invoking each recipe's build script:

| Variable | Description |
|----------|-------------|
| `CVC_PREFIX` | Shared install prefix (all components) |
| `CVC_SOURCE_DIR` | Extracted source tree for this component |
| `CVC_BUILD_DIR` | Out-of-source build directory |
| `CVC_INSTALL_DIR` | Per-component install dir (= prefix) |
| `CVC_DEPS_PREFIX` | Where to find previously-built deps |
| `CVC_PLATFORM` | `linux`, `macos`, or `windows` |
| `CVC_CONFIG` | `release` or `debug` |
| `CVC_LINK` | `shared` or `static` |
| `CVC_COMPONENT` | Recipe name |
| `CVC_VERSION` | Upstream version string |
| `CVC_RECIPE_DIR` | Path to the recipe directory |
| `CMAKE_BUILD_TYPE` | `Release` or `Debug` |
| `BUILD_SHARED_LIBS` | `ON` or `OFF` |

Each platform's env helper (`recipes/_common/env-{linux,macos,windows}.{sh,ps1}`)
sources these and provides the `cvc_cmake_build` / `Invoke-CvcCMakeBuild`
helper function that handles `cmake -G Ninja` configure + build + install
in one call.

## Verifying a local build

After `cvcpkg build-all` completes, the prefix should contain
cmake package configs for every component. Quick smoke test:

```sh
cmake -B /tmp/smoke -S /dev/null \
  -DCMAKE_PREFIX_PATH="$PWD/prefix" \
  -DCMAKE_FIND_PACKAGE_NO_PACKAGE_REGISTRY=ON \
  -DFIND_PKGS="Boost;FFTW3;GSL;HDF5;TIFF;OpenSSL;Protobuf;gRPC;Qt6"

# Each find_package should succeed (check CMakeOutput.log)
```

Or use `cvcpkg validate` to check recipe schemas and dependency
graph consistency:

```sh
cvcpkg validate --recipes-dir recipes
```
