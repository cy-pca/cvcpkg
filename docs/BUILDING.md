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

## Linux (glibc ≥ 2.35: Ubuntu 22.04+, Debian 12+)

### Compiler toolchain (from the system)

The system C/C++ compiler is the one thing a build takes from the
distribution rather than cvcpkg, together with the development files of
the host's own C library:

| What | Debian/Ubuntu package | Why it comes from the system |
|------|-----------------------|------------------------------|
| GCC (C/C++) | `gcc g++` | The compiler. Pulls in binutils (`as`, `ld`), which cvcpkg's `gfortran` drives too. The build fleet compiles with GCC 11.4, Ubuntu 22.04's |
| C library headers and startup files | `libc6-dev` | glibc's headers, `crt*.o` and `libc.so` link script have to be the host's own, since every bundle links against the glibc it is built on. Pulls in the kernel headers (`linux-libc-dev`) |

```sh
sudo apt-get install -y --no-install-recommends gcc g++ libc6-dev
```

(`gcc gcc-c++ glibc-devel` on Fedora/RHEL.)

Linux bundles are checked against a glibc floor of 2.35, Ubuntu 22.04's
and the oldest on the build fleet: `cvcpkg pack` fails a bundle that needs
newer glibc symbols. To build only for your own machine on a newer glibc,
set `CVCPKG_GLIBC_FLOOR` to its version (`ldd --version`).

### Build tools (from cvcpkg)

Everything else comes from cvcpkg: no apt build tools, no pip-installed
cmake, no system copies.

| Tool | cvcpkg component | Notes |
|------|------------------|-------|
| CMake | `cmake` (3.31.7) | Pinned < 4.x for Boost compat |
| Ninja | `ninja` | CMake generator |
| GNU Make | `make` | OpenSSL and autotools-based recipes; CMake's default generator |
| pkg-config | `pkg-config` (pkgconf) | Find modules, configure scripts |
| patchelf | `patchelf` | Rewrites shared bundles' RPATHs to `$ORIGIN` when they are packed. `cvcpkg build-all` builds it from its recipe; otherwise cvcpkg looks in the prefix, then on PATH |
| Meson | `meson` | Runs under the `python3` on PATH (numpy, scipy, postgresql-*) |
| m4, flex, bison | `m4`, `flex`, `bison` | Parser generators (postgresql-*, libpq); m4 also serves bison and libtool |
| GFortran | `gfortran` (11.4.0) | Not on PATH: `openblas` declares it in `depends.build`, so `cvcpkg install-deps` puts it in the prefix. Built to match the fleet's GCC 11.4, so its objects and runtime libraries mix with the C compiler's |
| Autotools | `autoconf`, `automake`, `libtool` | Not on PATH: the recipes that need them (`nfft3`) declare them in `depends.build` |

Install them into a tools prefix, then put **only the tools** on PATH,
the way `source-fallback-ci.yml` and `Dockerfile.test` do. CMake
searches its own install prefix for packages, and that prefix also holds
the curl/openssl headers and libraries cvcpkg installed with cmake, which
a recipe build would otherwise pick up instead of its declared deps.
`bin/cmake` loads libcurl, libssl and libcrypto through its RUNPATH
`$ORIGIN/../lib`, so those three libraries come along under their
versioned names (which `find_library` never matches). bison has the
absolute paths of its build-time data directory and m4 compiled in, and
flex runs `/usr/bin/m4` unless told otherwise, so `M4` and
`BISON_PKGDATADIR` point them at the tree:

```sh
cvcpkg install cmake ninja make pkg-config patchelf meson m4 flex bison \
  --prefix ~/cvcpkg-tools-prefix --config release --link shared
p=~/cvcpkg-tools-prefix t=~/cvcpkg-tools
mkdir -p "$t/bin" "$t/lib" "$t/share"
for exe in cmake ctest cpack ninja make pkgconf patchelf meson m4 flex bison; do
  cp "$p/bin/$exe" "$t/bin/"
done
ln -s pkgconf "$t/bin/pkg-config"
ln -s flex "$t/bin/flex++"
cp -R "$p"/share/cmake-* "$p/share/bison" "$t/share/"
cp -R "$p/lib/meson" "$t/lib/"
cp -P "$p"/lib/libcurl.so.4* "$p/lib/libssl.so.3" "$p/lib/libcrypto.so.3" "$t/lib/"
export PATH="$t/bin:$PATH" M4="$t/bin/m4" BISON_PKGDATADIR="$t/share/bison"
```

If the distribution's cmake, ninja, make or pkg-config are installed,
they are only shadowed; `command -v cmake` should name the tree.

### Interpreters

| Tool | Notes |
|------|-------|
| Python 3 | The system's `python3` (≥ 3.10) runs cvcpkg itself (`pip install` of this repo) and meson |
| Perl | Not on PATH: `openssl`, `qt6`, `autoconf` and `automake` declare cvcpkg's `perl` in `depends.build` |

### System libraries (from cvcpkg)

No X11, OpenGL or font `-dev` packages either. libx11, the xcb
family, libxext, libxi, libxrender, libxt, libglvnd, mesa, glu, glew,
fontconfig, freetype, harfbuzz and the rest are cvcpkg recipes, and the
recipes that build against them declare them (`qt6`: libx11, libxcb and
the xcb-util family, libxi, libxext, libxfixes, libxrender, libglvnd,
xkbcommon; `vtk`: libx11, libxext, libxt, libglvnd, glu), so
`cvcpkg install-deps` and `cvcpkg build-all` put them in the prefix.

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
