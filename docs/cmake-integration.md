# CMake integration

`cvcpkg install` populates a **prefix** — a directory with `include/`,
`lib/`, `lib/cmake/`, `lib/pkgconfig/`, etc. — that downstream CMake
projects consume. This guide covers the three ways to wire it in.

## TL;DR

```bash
cvcpkg install boost hdf5 qt6 --prefix ./deps
cmake -B build -DCMAKE_PREFIX_PATH="$PWD/deps"
```

Every dependency you installed now resolves from `./deps` via the normal
`find_package()` calls.

## 1. `find_package(cvcpkg)` (recommended)

On install, cvcpkg writes a package config into the prefix:

```
<prefix>/lib/cmake/cvcpkg/cvcpkgConfig.cmake
<prefix>/lib/cmake/cvcpkg/cvcpkgConfigVersion.cmake
<prefix>/lib/cmake/libcvc-deps/libcvc-depsConfig.cmake   # compat wrapper
```

So a downstream project can do:

```cmake
cmake_minimum_required(VERSION 3.16)
project(myapp)

find_package(cvcpkg CONFIG REQUIRED)   # prepends the prefix to CMAKE_PREFIX_PATH

find_package(Boost REQUIRED COMPONENTS filesystem system)
find_package(HDF5  REQUIRED COMPONENTS C CXX)
find_package(Qt6   REQUIRED COMPONENTS Core Gui Widgets)

target_link_libraries(myapp PRIVATE Boost::filesystem HDF5::HDF5 Qt6::Widgets)
```

`find_package(cvcpkg CONFIG REQUIRED)` prepends `<prefix>` to
`CMAKE_PREFIX_PATH` and sets `PKG_CONFIG_PATH` (for Meson/Autotools
sub-builds), so the subsequent `find_package()` calls resolve inside the
prefix. You still need CMake to be able to find `cvcpkgConfig.cmake` itself
— point it at the prefix once:

```bash
cmake -B build -DCMAKE_PREFIX_PATH="$PWD/deps"
```

### Backward compatibility

Projects written against the old name keep working:

```cmake
find_package(libcvc-deps CONFIG REQUIRED)
```

The generated `libcvc-depsConfig.cmake` loads `cvcpkgConfig.cmake` from the
same prefix and also exports `LIBCVC_DEPS_ROOT_DIR` / `LIBCVC_DEPS_VERSION`.

## 2. Toolchain file

To make a prefix authoritative for an entire configure (including
cross-compiles), pass the toolchain file. `cvcpkg install` does **not**
write it into the prefix — `<prefix>/share/cmake/cvcpkg/` only exists when
`cvcpkg` itself was installed via `cmake --install` (not the common case for
consumers). For a `cvcpkg install`-populated deps prefix, use the copy
shipped in this repo at
[`cmake/cvcpkg-toolchain.cmake`](../cmake/cvcpkg-toolchain.cmake) and point
it at the prefix explicitly with `-DCVCPKG_PREFIX`:

```bash
cmake -B build \
  -DCVCPKG_PREFIX="$PWD/deps" \
  -DCMAKE_TOOLCHAIN_FILE=/path/to/cvcpkg-toolchain.cmake
```

## 3. Activation script

`cvcpkg install` also writes activation scripts into the prefix. Sourcing
one sets `PATH`, `CMAKE_PREFIX_PATH`, `PKG_CONFIG_PATH`, and the platform
library path for the current shell:

```bash
source ./deps/bin/activate      # bash / zsh
cmake -B build                  # CMAKE_PREFIX_PATH already set
```

```powershell
. .\deps\Scripts\Activate.ps1   # Windows PowerShell
```

## pkg-config

The prefix ships `.pc` files under `<prefix>/lib/pkgconfig`. Any of the
options above set `PKG_CONFIG_PATH`, so Meson/Autotools consumers work too:

```bash
source ./deps/bin/activate
pkg-config --cflags --libs zlib
```
