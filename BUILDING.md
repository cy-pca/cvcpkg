# Building from source

This document lists every tool and system dependency needed to
reproduce a full `cvcpkg build-all` run on each supported platform.
The CI workflow
([`.github/workflows/recipe-build.yml`](.github/workflows/recipe-build.yml))
installs exactly these prerequisites, so the lists below always
match what CI uses.

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

### Build systems & utilities

Install via Homebrew:

```sh
brew install ninja autoconf automake libtool pkg-config libomp
```

| Tool | Homebrew formula | Notes |
|------|-----------------|-------|
| Ninja | `ninja` | CMake generator |
| Autotools | `autoconf automake libtool` | NFFT3 |
| pkg-config | `pkg-config` | Find modules |
| libomp | `libomp` | OpenMP runtime for FFTW3 threads |

### CMake

Boost 1.86's CMake files are incompatible with CMake 4.x, so pin
to 3.x:

```sh
pip3 install --break-system-packages "cmake>=3.31,<4"
```

### Interpreters

| Tool | Notes |
|------|-------|
| Python 3 | Ships with macOS / Homebrew |
| Perl | Ships with macOS (used by OpenSSL Configure, Qt6) |

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

### Build systems

| Tool | Install | Notes |
|------|---------|-------|
| CMake | `choco install cmake --version=3.31.7 --installargs 'ADD_CMAKE_TO_PATH=System' -y` | Pinned < 4.x for Boost compat |
| Ninja | `choco install ninja -y` | CMake generator |
| nmake | Visual Studio | OpenSSL build |

### Assembler

| Tool | Install | Notes |
|------|---------|-------|
| NASM | `choco install nasm -y` | OpenSSL and libjpeg-turbo use NASM for optimised x86_64 assembly. Install dir: `C:\Program Files\NASM` — ensure it is on PATH. |

After installing, add NASM to your PATH:

```powershell
$env:PATH = "C:\Program Files\NASM;$env:PATH"
```

Without NASM, OpenSSL falls back to unoptimised C code (`no-asm`)
and libjpeg-turbo disables SIMD, resulting in significantly slower
crypto and JPEG operations.

### Interpreters

| Tool | Install | Notes |
|------|---------|-------|
| Python 3 | `choco install python3 -y` | Runs cvcpkg |
| Perl | Strawberry Perl (pre-installed on GitHub runners, or `choco install strawberryperl -y`) | OpenSSL Configure |

### Package manager

| Tool | Install | Notes |
|------|---------|-------|
| vcpkg | Pre-installed at `C:\vcpkg` on GitHub runners. For local builds: `git clone https://github.com/microsoft/vcpkg && .\vcpkg\bootstrap-vcpkg.bat` | Used by `clapack` and `pthreads4w` recipes |
| Chocolatey | https://chocolatey.org/install | Installs cmake, ninja, nasm |

### Qt6 (pre-built)

On Windows, Qt6 is installed via `jurplel/install-qt-action` in CI
(version `6.7.*`, arch `win64_msvc2019_64`). For local builds,
install Qt 6.7.x from the [Qt online
installer](https://www.qt.io/download-qt-installer-oss) and set
`Qt6_DIR` to point at the MSVC kit, e.g.:

```powershell
$env:Qt6_DIR = "C:\Qt\6.7.3\msvc2019_64\lib\cmake\Qt6"
```

### One-liner install (Chocolatey)

From an **elevated PowerShell** (Administrator):

```powershell
choco install -y cmake --version=3.31.7 --installargs 'ADD_CMAKE_TO_PATH=System'
choco install -y ninja nasm strawberryperl python3
# Add NASM to PATH for the current session
$env:PATH = "C:\Program Files\NASM;$env:PATH"
```

Then open a **Developer PowerShell for VS 2022** to get `cl.exe`
and `nmake` on PATH.

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
| vcpkg | `clapack` (Windows), `pthreads4w` (Windows) |
| pkg-config | `nfft3`, various `find_package` module-mode lookups |
| patchelf | Post-build RPATH fixup (Linux shared only) |
| libomp | `fftw3` (macOS — OpenMP threading) |

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
