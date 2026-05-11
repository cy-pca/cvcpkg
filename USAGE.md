# Using libcvc-deps

This page is the practical reference for plugging a `libcvc-deps`
release into a CMake build.

## Quick start

1. Download the archive that matches the host you're building on
   from <https://github.com/transfix/libcvc-deps/releases>.

2. Extract it. The tree is self-contained:

   ```text
   libcvc-deps-<ver>-<os>-<arch>-<config>[-static]/
   ├── bin/            # .dll (Windows), Qt tools (qmake, moc, rcc, uic, …)
   ├── include/        # boost/, hdf5/, gsl/, log4cplus/, CGAL/, QtCore/, vtkCommonCore-9.5/, …
   ├── lib/            # .so / .dylib / .lib / .a
   │   └── cmake/      # <Pkg>Config.cmake for every bundled dep
   ├── plugins/        # Qt platform / image-format plugins
   └── share/
       └── cmake/libcvc-deps/  # libcvc-depsConfig.cmake
   ```

3. Point your CMake invocation at it via `CMAKE_PREFIX_PATH`:

   ```sh
   cmake -S libcvc -B build -G Ninja \
     -DCMAKE_PREFIX_PATH="$PWD/libcvc-deps-1.0.0-linux-x86_64-release-shared" \
     -DCMAKE_BUILD_TYPE=Release \
     -DCVC_BUILD_VOLROVER3=ON
   ```

## Matching archive to build config

| Want to build…              | Pick this archive flavor                |
|-----------------------------|-----------------------------------------|
| `libcvc` Release shared     | `release-shared`                        |
| `libcvc` Debug shared       | `debug-shared`                          |
| `libcvc` Release static     | `release-static` *(best-effort — see caveats)* |
| `libcvc` Debug static       | `debug-static` *(best-effort — see caveats)*   |
| `volrover3` Release         | `release-shared` (Qt does not support static linking gratis) |

On Windows the build type **must** match: a Release archive uses
the `/MD` runtime and ships release-only DLLs; a Debug archive
uses `/MDd` and ships Debug DLLs + PDBs. Mixing them at link time
will fail with mismatched-CRT errors.

### Static-flavor caveats

The `-static` flavor is **best-effort**, not strict. Not every
dependency has a usable static distribution, so the static archives
mix `.a`/`.lib` static archives where possible with a small set of
shared libraries that the upstream projects do not provide statically:

| Dependency        | Linux `-static` | macOS `-static`     | Windows `-static`             |
|-------------------|-----------------|---------------------|-------------------------------|
| Boost             | static `.a`     | static `.a`         | static `.lib` (vcpkg)         |
| HDF5              | static `.a`     | static `.a`         | static `.lib` (vcpkg)         |
| FFTW3             | static `.a`     | static `.a`         | static `.lib` (vcpkg)         |
| GSL               | static `.a`     | static `.a`         | static `.lib` (vcpkg)         |
| GMP / MPFR        | static `.a`     | static `.a`         | static `.lib` (vcpkg)         |
| NFFT3             | static `.a`     | static `.a` + dylib | **DLL + import lib only** (mingw `.a` is incompatible with MSVC) |
| LAPACK / BLAS     | static `.a`     | dylib only          | static `.lib` (vcpkg)         |
| log4cplus         | static `.a`     | dylib only          | static `.lib` (vcpkg)         |
| ImageMagick       | static + shared | dylib only          | static `.lib` (vcpkg)         |
| CGAL              | header-only     | header-only         | header-only                   |
| Eigen3            | header-only     | header-only         | header-only                   |
| **Qt6**           | **shared only** | **shared only**     | **shared only**               |
| **VTK 9.5**       | **shared only** | **shared only**     | **shared only**               |
| GLEW              | static + shared | n/a                 | static `.lib` (vcpkg)         |

**Qt6 is always shared** in every archive flavor. Upstream Qt does not
publish a static distribution under the open-source license without a
commercial agreement, and our pipeline uses the prebuilt Qt binaries
from `aqtinstall` (Windows/macOS) and the distro `qt6-base-dev`
package (Linux). A fully static Qt would require an in-pipeline
source build with non-trivial configuration.

**VTK 9.5 is always shared** because we build it from source once
per OS with `BUILD_SHARED_LIBS=ON` to keep the build cache compact.
VTK's static build is fragile when Qt is involved (many rendering
modules don't support it), so we have not invested in a separate
static cache lane.

If you need true full-static linking — including Qt6 and VTK — you
will need to build those two dependencies yourself; the `libcvc-deps`
static flavor still saves you the build cost for Boost, HDF5, FFTW,
NFFT3, LAPACK/BLAS, CGAL, Eigen3, and friends.

## Using from libcvc CI

In a future iteration of libcvc's own `release.yml`, the bulk of
the install-deps / build-VTK steps can be replaced with:

```yaml
- name: Download libcvc-deps
  run: |
    VER=1.0.0
    STEM=libcvc-deps-${VER}-linux-x86_64-${BTLC}${LINKSFX}
    curl -fL -o deps.tar.gz \
      "https://github.com/transfix/libcvc-deps/releases/download/v${VER}/${STEM}.tar.gz"
    tar xzf deps.tar.gz
    echo "CMAKE_PREFIX_PATH=$PWD/${STEM}" >> "$GITHUB_ENV"
```

…and equivalents for `.zip` archives on macOS / Windows.

## Using from downstream apps (F2Dock, MolSurf, etc.)

```cmake
find_package(libcvc-deps CONFIG REQUIRED)  # smoke check, optional
find_package(Boost REQUIRED COMPONENTS thread filesystem system)
find_package(HDF5 REQUIRED COMPONENTS C CXX)
find_package(VTK 9.5 REQUIRED COMPONENTS CommonCore RenderingQt …)
find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets OpenGL OpenGLWidgets)
find_package(cvc CONFIG REQUIRED)
```

…and configure with both prefixes:

```sh
cmake -S app -B build \
  -DCMAKE_PREFIX_PATH="$DEPS_ROOT;$LIBCVC_ROOT"
```

## Component pins

See [`README.md`](README.md#version-pins) for the upstream versions
captured by a given `libcvc-deps` release.

## Host requirements

Each archive aims to be self-contained, but a small set of
ABI-stable, host-supplied libraries is *not* bundled:

| Platform | Host-supplied (not in archive)                                  |
|----------|-----------------------------------------------------------------|
| Linux    | glibc, `libstdc++.so.6`, `libgcc_s.so.1`, the dynamic loader, libpthread/libdl/librt/libm/libresolv (all part of glibc) |
| macOS    | The system libc / libc++ shipped with the OS (everything else is rewritten via `dylibbundler` to `@loader_path/`) |
| Windows  | The Universal C Runtime (UCRT). All other DLLs (Qt, VTK, boost, zlib, libpng, …) are in `bin/`. |

### Linux: bundled transitive deps

The Linux Release pipeline runs an `ldd` sweep after staging the
allowlisted libraries and copies every transitively-NEEDED `.so`
into `lib/` (with SONAME symlinks), then sets `RPATH=$ORIGIN` on
every shipped `.so`. This means the bundle is portable across
Ubuntu LTS versions (and most other glibc-based distros) regardless
of mismatches in `libicu`, `libpng`, `libxml2`, `liblzma`, `libssl`,
`libfreetype`, `libfontconfig`, `libharfbuzz`, etc.

You should only need a recent-ish glibc on the host. Practical
floor: glibc ≥ 2.31 (Ubuntu 20.04). The archive is built on
`ubuntu-latest` GitHub runners (currently 22.04, glibc 2.35); any
host with glibc ≥ that should be fully covered.

### Static archives

The `-static` flavors ship `.a` / `.lib` only and never invoke the
`ldd` / `dylibbundler` step. Transitive runtime deps are not a
concern because everything is linked into the final consumer
binary at static-link time. You are responsible for whatever the
static linker pulls in (e.g. system `-ldl`, `-lpthread`).
