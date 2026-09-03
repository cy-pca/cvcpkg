# Using libcvc-deps (legacy v1.x bundles)

> **Legacy document.** This page describes the retired v1.x monolithic
> `libcvc-deps-<version>` GitHub-Releases archives. New consumers should
> install per-component packages with `cvcpkg install` instead — see the
> [README](../README.md) and the
> [getting-started tutorial](getting-started-tutorial.md). This page is
> kept for projects still pinned to a v1.x archive; the Windows Qt
> deployment and runtime-library sections remain broadly applicable to
> cvcpkg prefixes too.

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
     -DCMAKE_BUILD_TYPE=Release
   ```

   (`-DCVC_BUILD_VOLROVER3=ON` no longer applies here — volrover3 was
   extracted out of the `libcvc` tree into its own `volrover` repo, where it
   consumes `libcvc` as an external SDK package rather than an in-tree
   `add_subdirectory`. See `volrover/volrover3/README.md` for its own build
   instructions, e.g. `cvcpkg build volrover3 --prefix ./deps`.)

## Matching archive to build config

| Want to build…              | Pick this archive flavor                |
|-----------------------------|-----------------------------------------|
| `libcvc` Release shared     | `release-shared`                        |
| `libcvc` Debug shared       | `debug-shared`                          |
| `libcvc` Release static     | `release-static` *(best-effort — see caveats)* |
| `libcvc` Debug static       | `debug-static` *(best-effort — see caveats)*   |

`volrover3` no longer builds from this archive — it lives in the `volrover`
repo now and consumes `libcvc`/`qt6`/`vtk` as published cvcpkg packages (see
above), not this GitHub-Releases bundle.

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
find_package(yaml CONFIG REQUIRED)
find_package(Protobuf CONFIG REQUIRED)
find_package(gRPC CONFIG REQUIRED)
find_package(VTK 9.5 REQUIRED COMPONENTS CommonCore RenderingQt …)
find_package(Qt6 REQUIRED COMPONENTS Core Gui Widgets OpenGL OpenGLWidgets)
find_package(cvc CONFIG REQUIRED)
```

…and configure with both prefixes:

```sh
cmake -S app -B build \
  -DCMAKE_PREFIX_PATH="$DEPS_ROOT;$LIBCVC_ROOT"
```

## Deploying a Windows app linked against libcvc-deps

Qt6 on Windows ships as a relocatable set of DLLs + plugins. The
libcvc-deps bundle places them at:

```text
<deps>/bin/Qt6Core.dll, Qt6Gui.dll, Qt6Widgets.dll, ...
<deps>/bin/qt.conf                  # [Paths] Prefix=..
<deps>/plugins/platforms/qwindows.dll
<deps>/plugins/imageformats/*.dll
<deps>/plugins/styles/*.dll
<deps>/bin/windeployqt.exe          # bundled Qt deployment tool
```

When your `.exe` lives inside `<deps>/bin/` it boots fine — Qt6Core
reads `<deps>/bin/qt.conf` and resolves plugins via `Prefix=..` →
`<deps>/plugins`. When your `.exe` lives anywhere else and you copy
only the Qt DLLs next to it, Qt can no longer find its plugins and
fails to start with:

```text
This application failed to start because no Qt platform plugin could
be initialized. Reinstalling the application may fix this problem.
Available platform plugins are: ...
```

### Qt6 plugin resolution order

`Qt6Core.dll` searches for its plugin directory in this order:

1. `qt.conf` next to the running `.exe` (`[Paths] Plugins=` / `Prefix=`).
2. `qt.conf` next to `Qt6Core.dll`.
3. The `QT_PLUGIN_PATH` environment variable.
4. The absolute path baked into `Qt6Core.dll` at build time. For our
   bundle that is `D:\a\libcvc-deps\Qt\6.7.3\msvc2019_64\plugins`,
   which **does not exist on your machine** — so (4) never works for
   consumers.

You need to make one of (1)–(3) point at a real plugin tree.

### Recommended: `windeployqt`

Run the bundled deployment tool against your built `.exe`. It copies
every Qt DLL the .exe needs (Core, Gui, Widgets, OpenGL, …) plus the
required plugin directories (`platforms/qwindows.dll`, image-format
plugins, styles, …) next to the executable and writes a working
`qt.conf`:

```bat
set DEPS=C:\path\to\libcvc-deps-1.0.0-windows-x86_64-release
%DEPS%\bin\windeployqt.exe path\to\myapp.exe
```

For Debug builds use the Debug archive's `windeployqt.exe` against
your Debug `.exe` so the Debug-flavor (`*d.dll`) DLLs and plugins
are deployed.

CMake integration via a post-build step:

```cmake
if(WIN32)
  find_program(WINDEPLOYQT_EXECUTABLE windeployqt
    HINTS "${Qt6_DIR}/../../../bin")
  add_custom_command(TARGET myapp POST_BUILD
    COMMAND "${WINDEPLOYQT_EXECUTABLE}"
            --no-translations --no-system-d3d-compiler
            "$<TARGET_FILE:myapp>")
endif()
```

### Alternatives

If you don't want `windeployqt`, any *one* of these is enough:

- **Copy the plugin tree next to the .exe**: drop
  `<deps>\plugins\` so the layout is `myapp.exe` +
  `./plugins/platforms/qwindows.dll`. Qt finds it by default.
- **Set `QT_PLUGIN_PATH`** before launching:
  ```bat
  set QT_PLUGIN_PATH=C:\path\to\libcvc-deps\plugins
  myapp.exe
  ```
- **Drop a `qt.conf` next to your .exe** with an absolute prefix:
  ```ini
  [Paths]
  Prefix=C:/path/to/libcvc-deps
  Plugins=plugins
  ```

In all three cases the Qt6 DLLs (`Qt6Core.dll`, …) still have to be
locatable at load time — either next to the .exe, or via a
`<deps>\bin` entry on `PATH`.

### Debugging plugin discovery

If a launch still fails, set `QT_DEBUG_PLUGINS=1` and re-run. Qt
will print every directory it searched and the reason each
candidate plugin was rejected (e.g. release Qt loading debug
plugin, missing transitive DLL, wrong architecture):

```bat
set QT_DEBUG_PLUGINS=1
myapp.exe
```

The first "Cannot load library …" line names a path you can fix
directly.

### Common pitfalls

- **Mixing Debug and Release.** A Release `.exe` will refuse to load
  the `*d.dll` plugins (and vice versa). Use the matching libcvc-deps
  archive flavor (`-release` / `-debug`) for your build type.
- **Stale Qt on `PATH`.** If another Qt install (e.g. a system Qt5,
  or an old aqtinstall tree) is earlier on `PATH`, its `Qt6Core.dll`
  may load instead of the bundle's. Either prepend `<deps>\bin`
  to `PATH` or rely on the side-by-side DLLs that `windeployqt`
  copies.
- **D3D compiler / OpenGL drivers.** Qt's `windows` platform plugin
  needs `d3dcompiler_47.dll` for QtQuick / QML; `windeployqt`
  copies it automatically. For pure QtWidgets apps it's not
  required.

## Component pins

See the [release notes](../CHANGELOG.md) for the upstream versions
captured by a given `libcvc-deps` release.

## Host requirements

Each archive aims to be self-contained, but a small set of
ABI-stable, host-supplied libraries is *not* bundled:

| Platform | Host-supplied (not in archive)                                  |
|----------|-----------------------------------------------------------------|
| Linux    | glibc (`libc.so.6`, `libpthread.so.0`, `libdl.so.2`, `librt.so.1`, `libm.so.6`, `libresolv.so.2`, `libnsl.so.1`, `libutil.so.1`, `libanl.so.1`) and the dynamic loader (`ld-linux-x86-64.so.2`). **`libstdc++.so.6` and `libgcc_s.so.1` ARE bundled** (since v1.0.2). |
| macOS    | The system libc / libc++ shipped with the OS (everything else is rewritten via `dylibbundler` to `@loader_path/`). |
| Windows  | **Universal C Runtime only** (`ucrtbase.dll`, `api-ms-win-crt-*.dll`) — part of Windows 10/11. The MSVC C/C++ runtime DLLs (`msvcp140*.dll`, `vcruntime140*.dll`, `concrt140.dll`) ARE bundled in Release archives (since v1.0.2). Debug archives still require Visual Studio 2022 or the Windows SDK debug runtime on the consumer machine. |

## Runtime libraries (C / C++ / Fortran runtimes)

libcvc-deps ships compiler runtime DLLs/SOs alongside the bundled
libraries so that consumers do not have to coordinate redistributable
installs separately. Policy per platform:

### Linux (shared bundles)

- **glibc** (`libc.so.6`, `libpthread.so.0`, …): host-supplied. glibc
  is forward-compatible — a binary built on the GitHub Actions
  Ubuntu runner runs on any host with glibc ≥ the runner's glibc.
  Floor as of v1.0.2: glibc ≥ 2.39 (Ubuntu 24.04).
- **libstdc++.so.6, libgcc_s.so.1**: **bundled** in `lib/` and
  resolved via `RPATH=$ORIGIN`. The runner's GCC 13 libstdc++ is
  newer than (or equal to) anything a typical consumer ships, and
  libstdc++ is forward-compatible, so the bundled copy satisfies
  all our `.so`'s without depending on the host distro's GCC age.
- **libgfortran.so.5**: **bundled** (LAPACK has an explicit Fortran
  runtime dependency; libgfortran has no compatibility guarantee
  across distros, so we always ship it).
- **libgomp.so.1** (OpenMP runtime): **bundled** via the transitive
  ldd sweep when any shipped library NEEDs it.

If your application also links against the system's libstdc++ via
its own toolchain (e.g. you compile `myapp.exe` with GCC on the
host), prepend the bundle's `lib/` to your runtime library search
path so the loader does not pick a mix of two libstdc++ copies:

```sh
export LD_LIBRARY_PATH="$DEPS/lib:${LD_LIBRARY_PATH:-}"
```

Or, preferred, set `RPATH=$ORIGIN/../lib` (or an absolute path) on
your own binary at link time:

```cmake
set_target_properties(myapp PROPERTIES
  BUILD_RPATH   "$<TARGET_FILE_DIR:myapp>/../lib"
  INSTALL_RPATH "\$ORIGIN/../lib")
```

### Windows (Release bundles)

- **Universal CRT** (`ucrtbase.dll`, `api-ms-win-crt-*.dll`):
  host-supplied — it's part of Windows 10 and 11. Not bundled.
- **MSVC 2015–2022 C/C++ runtime**: **bundled** in `bin/`. The
  following DLLs are staged from the build runner's Visual Studio
  install under `VC\Redist\MSVC\<ver>\x64\Microsoft.VC143.CRT\`:
  `msvcp140.dll`, `msvcp140_1.dll`, `msvcp140_2.dll`,
  `msvcp140_atomic_wait.dll`, `msvcp140_codecvt_ids.dll`,
  `vcruntime140.dll`, `vcruntime140_1.dll`, `concrt140.dll`.
  App-local deployment of these DLLs is explicitly permitted by
  [Microsoft's redistribution license](https://learn.microsoft.com/en-us/visualstudio/productinfo/2022-redistribution-vs).
  Consumers no longer need the *Visual C++ Redistributable for
  Visual Studio 2015–2022* installer.
- **MinGW C runtime** (used by NFFT3 only): **bundled** in `bin/`:
  `libgcc_s_seh-1.dll`, `libwinpthread-1.dll`, `libgomp-1.dll`,
  `libstdc++-6.dll`, plus `libfftw3-3.dll` / `libfftw3_threads-3.dll`
  (FFTW's own DLLs that NFFT depends on).

A small `share/msvc-crt-redist.txt` manifest inside Release
archives records the exact MSVC toolset version the bundled CRT
DLLs came from, for compliance / audit purposes.

### Windows (Debug bundles)

- **The debug CRT (`msvcp140d.dll`, `vcruntime140d.dll`,
  `ucrtbased.dll`, `concrt140d.dll`) is NOT redistributable** under
  Microsoft's license and is therefore **not** bundled in Debug
  archives. Consumers of Debug archives must build on a machine
  with Visual Studio 2022 or the Windows 10/11 SDK debug runtime
  installed. This is the intended use of Debug bundles (developer
  link-time consumption, not end-user deployment).

### macOS

- All system runtimes (`libSystem.B.dylib`, `libc++.1.dylib`, …)
  are part of the OS and are not bundled. Apple guarantees forward
  compatibility within each macOS major version.
- The bundled `dylib` install names are rewritten via
  `dylibbundler` to `@rpath/…` so the consumer-side `RPATH=@loader_path`
  resolves them inside the archive.
- **`libgfortran` is not present** because macOS LAPACK is the
  Accelerate framework, which has no Fortran symbols.

### Static archives

The `-static` flavors ship `.a` / `.lib` only and never invoke the
ldd / dylibbundler / CRT-staging steps. Transitive runtime deps
are not a concern in the archive because everything is linked into
the final consumer binary at static-link time. You are responsible
for whatever the static linker pulls in (e.g. system `-ldl`,
`-lpthread`, `kernel32.lib`).

> **Windows note:** the `windows-x86_64-*-static` flavor is **not
> produced** for v1.0.x. vcpkg's `mpfr` port hangs indefinitely on
> the `x64-windows-static` triplet under hosted GitHub Actions
> Windows runners. See `known-issues.md` for the diagnosis
> and re-enablement plan. Windows users should consume the
> shared bundles (`windows-x86_64-debug.zip` /
> `windows-x86_64-release.zip`).

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
`ubuntu-latest` GitHub runners (currently 24.04, glibc 2.39); any
host with glibc ≥ that should be fully covered.
