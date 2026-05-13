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
| `libcvc` Release static     | `release-static`                        |
| `libcvc` Debug static       | `debug-static`                          |
| `volrover3` Release         | `release-shared` (Qt does not support static linking gratis) |

On Windows the build type **must** match: a Release archive uses
the `/MD` runtime and ships release-only DLLs; a Debug archive
uses `/MDd` and ships Debug DLLs + PDBs. Mixing them at link time
will fail with mismatched-CRT errors.

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

> **Windows note:** the `windows-x86_64-*-static` flavor is **not
> produced** for v1.0.0. vcpkg's `mpfr` port hangs indefinitely on
> the `x64-windows-static` triplet under hosted GitHub Actions
> Windows runners. See `docs/known-issues.md` for the diagnosis
> and re-enablement plan. Windows users should consume the
> shared bundles (`windows-x86_64-debug.zip` /
> `windows-x86_64-release.zip`).
