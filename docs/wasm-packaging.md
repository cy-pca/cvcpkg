# WebAssembly with cvcpkg: building and packaging wasm apps

The guide for cvcpkg users who want to build — and optionally package — a
WebAssembly app on top of the cvcpkg catalog. Every step of the command flow
is a cvcpkg command: the Emscripten toolchain, the dependency closure, the
app's own build, and the distributable bundle.

The cvcGL targets in `libcvc` are the reference implementation of this flow;
this document generalizes them. The cvcGL-specific extras (WebGL state shim,
FrameYield, glsync) live in `libcvc/docs/CVCGL_WASM.md`; the complete
cvcGL build + package flow with `/tmp` prefixes is in
`libcvc/docs/FULL_BUILD_CVCPKG.md` → *Building and packaging wasm apps*.

## TL;DR

```bash
# 1. Toolchain + dependency closure, all from the catalog:
cvcpkg install emsdk --platform linux --prefix /tmp/myapp/emsdk
export CVC_EMSDK_DIR=/tmp/myapp/emsdk && source "$CVC_EMSDK_DIR/emsdk_env.sh"
cvcpkg install --from myapp-requirements-wasm-mt.yaml --prefix /tmp/myapp/deps

# 2. Build the app — one CMakeLists.txt serves native and wasm:
emcmake cmake -G Ninja -S . -B build-wasm-mt -DCMAKE_BUILD_TYPE=Release
cmake --build build-wasm-mt          # → myapp.js + myapp.wasm

# 3. Package it as a cvcpkg bundle (optional):
cvcpkg pack <path-to-recipe> --from-prefix /tmp/myapp/inst \
    --platform wasm-mt --config release --link static --local --output-dir dist
```

## The platforms

| Platform | Threads | Runtime | Use when |
| --- | --- | --- | --- |
| `wasm` | none | browser / node | a single-threaded app; the easiest serving story (no special headers) |
| `wasm-mt` | `-pthread` + SharedArrayBuffer | browser, COOP/COEP | the app uses `std::thread`, OpenMP, or a threaded engine |
| `wasi` | (wasmtime) | wasmtime | CLI-style modules, not browser apps |

Rules that bite:

- **`wasm` and `wasm-mt` are not interchangeable.** Emscripten refuses to mix
  `-pthread` and non-`-pthread` objects at link time (`--shared-memory is
  disallowed`). A `wasm` bundle must never feed a `wasm-mt` consumer, or
  vice versa. The catalog keeps the two worlds separate; one `install` or
  requirements file targets exactly one of them.
- **Everything is static.** `recipes/_common/env-wasm.sh` forces
  `BUILD_SHARED_LIBS=OFF` for wasm builds, so wasm `install` / `build`
  commands pass `--link static` (a wasm static bundle is self-contained —
  `.wasm` + JS, no runtime deps).
- **Emscripten is a *host* tool.** The `emsdk` recipe cross-compiles *from*
  your host *to* wasm; install it for the host platform (`--platform linux`
  on a linux box) and point `CVC_EMSDK_DIR` at its prefix. When cvcpkg
  builds a recipe *targeting* `wasm` / `wasm-mt`, the toolchain is injected
  automatically (`emsdk` declares `cross_toolchain: target_platforms:
  [wasm, wasm-mt]`) and is never listed as a dependency.
- **Under `emcmake`, deps resolve via `CMAKE_FIND_ROOT_PATH`.**
  Emscripten's toolchain re-roots `find_package()` / `find_library()`
  searches under it, and the deps prefix is exactly where cvcpkg laid the
  closure. Set it on the command line (`-DCMAKE_FIND_ROOT_PATH=<deps>`) or
  from CMakeLists (`cvcpkg_wasm_deps()`, below).
- **`wasi` apps run under wasmtime, not node.** `recipes/_common/env-wasi.sh`
  expects `CVC_WASI_SDK_DIR`; `cvc_wasm_run.sh` picks the runner
  (`node` for `wasm`, `wasmtime` for `wasi`) from `CVC_PLATFORM`.

## Organizing a wasm-able project

Keep one source tree and let the platform come from the build, not from
forked CMake files:

```
myapp/
├── CMakeLists.txt                   # one file: native AND wasm
├── cmake/
│   └── cvcpkg-wasm-app.cmake        # vendored from the cvcpkg repo (below)
├── myapp-requirements-wasm-mt.yaml  # the wasm-mt dependency closure
├── myapp-requirements.yaml          # (optional) the native closure
├── src/
│   └── main.cpp
└── web/                             # (optional) host pages / index
    └── index.html
```

### The requirements file

cvcpkg requirements files (see `examples/f2dock-requirements.yaml`) pin
`platform`, `arch`, `config` and `link`, plus the `components:` list. Pin
the platform *in the file* and one command installs the whole closure for
that platform (`install --from` reads these fields; explicit CLI flags
override them, and a non-`auto` `arch` is derived from the target
platform — `wasm32` for `wasm` / `wasm-mt`):

```yaml
# myapp-requirements-wasm-mt.yaml
platform: wasm-mt
arch: auto            # derives wasm32 from the target platform
config: release
link: static

components:
  - cvc/libcvc        # org-qualified catalog bundles (or unqualified names)
  - cvc/cvcgl
  - boost
  - zlib
```

```bash
cvcpkg install --from myapp-requirements-wasm-mt.yaml --prefix /tmp/myapp/deps
# add --no-fallback-to-source to fail fast if a component has no wasm
# catalog entry instead of silently rebuilding it from source
```

Keep one file per platform variant (`myapp-requirements-wasm-mt.yaml` next
to the native default); that is the whole "organization" of the dependency
side.

### The CMakeLists.txt

The boilerplate below makes the *same* `CMakeLists.txt` work natively and
under `emcmake` — no `#if __EMSCRIPTEN__` scaffolding:

```cmake
cmake_minimum_required(VERSION 3.16)
project(myapp CXX)

include(${CMAKE_CURRENT_SOURCE_DIR}/cmake/cvcpkg-wasm-app.cmake)

# The cvcpkg wasm deps prefix -> CMAKE_FIND_ROOT_PATH (Emscripten re-roots
# find_* there). With no argument it uses $ENV{CVCPKG_WASM_DEPS}.
cvcpkg_wasm_deps()
cvcpkg_wasm_threads()              # wasm-mt only; no-op natively / single-threaded

find_package(cvcGL REQUIRED)        # resolves from the deps prefix

add_executable(myapp src/main.cpp)
target_link_libraries(myapp PRIVATE cvcGL::cvcGL)
cvcpkg_wasm_app(myapp)             # the browser-side link fixes; no-op natively
```

Call `cvcpkg_wasm_threads()` *before* any target in the directory is
created: it writes `CMAKE_C_FLAGS` / `CMAKE_CXX_FLAGS` /
`CMAKE_EXE_LINKER_FLAGS`, which is the CMake-native equivalent of what
`recipes/_common/env-wasm-mt.sh` does with `CFLAGS` / `CXXFLAGS` /
`LDFLAGS` — those env exports never reach `cmake`, so for a CMake app the
macro is what actually gets `-pthread` onto emcc's command line. Keep a
separate build directory per platform (`build`, `build-wasm`,
`build-wasm-mt`).

### Build

```bash
export CVC_EMSDK_DIR=/tmp/myapp/emsdk          # from step 1
source "$CVC_EMSDK_DIR/emsdk_env.sh"
export CVCPKG_WASM_DEPS=/tmp/myapp/deps        # or pass the prefix to cvcpkg_wasm_deps()

emcmake cmake -G Ninja -S . -B build-wasm-mt -DCMAKE_BUILD_TYPE=Release
cmake --build build-wasm-mt
# → build-wasm-mt/myapp.js + myapp.wasm (+ myapp.data if you preloaded files)
```

Data assets are baked in with `--preload-file` (a link option, e.g.
`target_link_options(myapp PRIVATE "--preload-file assets/scene:share/scene")`
— see the in-tree demos in `libcvc/src/cvcGL/examples/wasm/`, which bake
scenes and trained weights into the `.data`).

### Running and serving

- **Single-threaded (`wasm`)**: any static server will do. For CLI-style
  output you can also run it headlessly with the catalog's node: source
  `cvcpkg/recipes/_common/cvc_wasm_run.sh` with `CVC_PLATFORM=wasm` and
  `CVC_EMSDK_DIR` set, and use its `cvc_wasm_cc` / `cvc_wasm_run` helpers
  to cross-compile and execute a wasm binary directly.
- **Threaded (`wasm-mt`)**: the server must send the SharedArrayBuffer
  headers, or `SharedArrayBuffer` is `undefined` and the app dies at start:

  ```python
  # serve.py — minimal COOP/COEP static server
  import http.server

  class H(http.server.SimpleHTTPRequestHandler):
      def end_headers(self):
          self.send_header("Cross-Origin-Opener-Policy", "same-origin")
          self.send_header("Cross-Origin-Embedder-Policy", "require-corp")
          super().end_headers()

  http.server.ThreadingHTTPServer(("127.0.0.1", 8822), H).serve_forever()
  ```

  cvcGL bundles ship their own: `cvcgl-examples-web` runs a `serve.py`
  that sends exactly these headers and opens the browser.

## The boilerplate: `cmake/cvcpkg-wasm-app.cmake`

Shipped in the cvcpkg repo; vendor a copy into `cmake/` (or `include()` it
from a checkout of cvcpkg). Every entry point is a **no-op outside
Emscripten** (a build configured with `emcmake`), so the same
`CMakeLists.txt` stays native:

| Entry point | What it does |
| --- | --- |
| `cvcpkg_wasm_threads()` | Adds `-pthread` to the directory's C/C++ compile *and* linker flags (idempotent). Threaded deps with unthreaded app objects is the classic `--shared-memory is disallowed` link failure; this is the fix. |
| `cvcpkg_wasm_deps([PREFIX ...])` | Appends cvcpkg wasm deps prefixes to `CMAKE_FIND_ROOT_PATH`. With no argument: `$ENV{CVCPKG_WASM_DEPS}`, then `$ENV{CVC_WASM_DEPS}`. Appends only — it deliberately does not set `CMAKE_FIND_ROOT_PATH_MODE_*` (that would hide the Emscripten sysroot). |
| `cvcpkg_wasm_app(<target> ...)` | The browser-side link fixes for one target, with the keywords below. |

Keywords of `cvcpkg_wasm_app()`:

| Keyword | Default | Meaning |
| --- | --- | --- |
| `ASYNCIFY ON\|OFF` | `ON` | `-sASYNCIFY=1` — required for anything that yields (`emscripten_sleep`, VTK's in-render yield, a blocking fetch). `OFF` only for a hot loop that never yields. |
| `MEMORY_GROWTH ON\|OFF` | `ON` | `-sALLOW_MEMORY_GROWTH=1` — a browser page starts with a small heap and grows it on demand; a fixed heap is a common cause of OOM crashes that happen only in the browser. |
| `MIMALLOC AUTO\|ON\|OFF` | `AUTO` | `-sMALLOC=mimalloc`. `AUTO` = ON iff the target links `-pthread` (dlmalloc serialises every `malloc`/`free` on one global lock that threads contend on; mimalloc is per-thread) and never under `-fsanitize=address` (emcc refuses the combination). A pre-existing `-sMALLOC=` of your own wins. |
| `PTHREADS AUTO\|ON\|OFF` | `AUTO` | ON iff the target already links `-pthread` — re-detected from the combined link line, so it composes with `cvcpkg_wasm_threads()`; puts `-pthread` on this target's compile *and* link line. |
| `PRE_JS <file>...` | — | Extra `--pre-js` files (client JS that runs before the wasm starts); each is registered as a `LINK_DEPENDS` so an edited file re-links the app. |

A checkout may predate a keyword you want — feature-test it:

```cmake
if("PRE_JS" IN_LIST CVCPKG_WASM_APP_FEATURES)
```

### How it composes with cvcGL

cvcGL installs its own `cvcgl_wasm_app()` next to `cvcGLConfig.cmake` —
the cvcGL-specific half (the WebGL state shim as `--pre-js`, mimalloc
keyed on whether cvcGL itself was built `-pthread`, FrameYield locking).
A cvcGL app gets the generic half from this boilerplate and the cvcGL half
from the library:

```cmake
find_package(cvcGL REQUIRED)

add_executable(myapp src/main.cpp)
target_link_libraries(myapp PRIVATE cvcGL::cvcGL)
cvcgl_wasm_app(myapp)      # cvcGL: state shim, mimalloc, frame-yield lock
cvcpkg_wasm_app(myapp)    # generic: asyncify, memory growth, -pthread, pre-js
```

The two coexist: each respects flags the other added (a pre-existing
`-sMALLOC=`, a pre-existing `-pthread`), and both are no-ops natively. The
cvcGL contract — what the shim does, FrameYield's rules, the
`-sASYNCIFY_IGNORE_INDIRECT` checklist, glsync and `serve.py` — is in
`libcvc/docs/CVCGL_WASM.md`.

## Worked example: the libcvc / cvcGL wasm targets

The reference "build + package a wasm app" flow (the shape
`publish-cvcgl-wasm.yml` uses), run from the libcvc checkout with `/tmp`
prefixes:

```bash
# a. Toolchain + the wasm-mt static closure libcvc/cvcGL link (the catalog
#    list from publish-cvcgl-wasm.yml):
cvcpkg install cmake ninja --prefix /tmp/cvc-wasm/tools --config release
export PATH=/tmp/cvc-wasm/tools/bin:$PATH
cvcpkg install emsdk --platform linux --prefix /tmp/cvc-wasm/emsdk
export CVC_EMSDK_DIR=/tmp/cvc-wasm/emsdk
for p in zlib bzip2 xz zstd libpng libjpeg-turbo tiff freetype libwebp \
         libxml2 lerc assimp imagemagick boost imgui sdl3 vtk; do
  cvcpkg install "$p" --platform wasm-mt --arch wasm32 --config release \
      --link static --prefix /tmp/cvc-wasm/deps
done

# b. ONE emcmake configure builds + installs cvc and cvcGL (wasm-mt):
source "$CVC_EMSDK_DIR/emsdk_env.sh"
emcmake cmake -G Ninja -S . -B build-wasm \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/tmp/cvc-wasm/inst \
    -DCMAKE_FIND_ROOT_PATH=/tmp/cvc-wasm/deps \
    -DCVC_BUILD_CVCGL=ON -DCVC_WASM_PTHREADS=ON
cmake --build build-wasm --target cvc cvcGL -j
cmake --install build-wasm

# c. Package as cvcpkg bundles:
for r in libcvc cvcgl; do
  cvcpkg pack "cvcpkg/recipes/$r" --from-prefix /tmp/cvc-wasm/inst \
      --platform wasm-mt --config release --link static --local --output-dir dist
done
cvcpkg publish dist/libcvc-*-wasm-mt-*.tar.gz \
    --server https://cvcpkg.org --org cvc --token "$CVCPKG_TOKEN"
```

Or skip (a)–(b) entirely and consume the published bundles:

```bash
cvcpkg install cvc/libcvc cvc/cvcgl --platform wasm-mt --arch wasm32 \
    --link static --prefix /tmp/cvc-wasm/deps
```

The full narrative — including the `cvcgl-examples` gallery packaging
(`build-wasm.sh` + `build-pages.py`, the `cvcgl-examples-web` launcher)
and the in-tree `build-wasm-demo.sh` dev loop for the 9 gallery demos +
`ariadne_hello` — is in `libcvc/docs/FULL_BUILD_CVCPKG.md` → *Building
and packaging wasm apps*; the CI reference is
`libcvc/.github/workflows/publish-cvcgl-wasm.yml`.

## Packaging your own wasm app as a cvcpkg bundle

1. Give the app a recipe (see `docs/recipe-authoring.md`):
   `source.type: vendored`, a `wasm-mt` (and/or `wasm`) matrix entry whose
   build script runs the `emcmake` flow above, and the closure as
   `depends:`.
2. Build + install into a *clean* prefix (`/tmp/myapp/inst` — separate
   from the deps prefix, so the bundle archives only *your* app).
3. Archive it against the recipe's metadata:

   ```bash
   cvcpkg pack <path-to-recipe> --from-prefix /tmp/myapp/inst \
       --platform wasm-mt --config release --link static \
       --local --output-dir dist
   ```

   `--from-prefix` skips the build and packages the installed tree: name,
   version, deps and cmake packages come from the recipe.
4. Publish **by archive path** (the manifest carries the platform —
   publish-by-name would re-derive the *host* arch):

   ```bash
   cvcpkg publish dist/myapp-<ver>-wasm-mt-release-static.tar.gz \
       --server https://cvcpkg.org --org <org> --token "$CVCPKG_TOKEN"
   ```

5. Consumers get the one-liner:

   ```bash
   cvcpkg install myapp --platform wasm-mt --prefix /tmp/myapp
   /tmp/myapp/bin/myapp-web        # a launcher you ship, à la cvcgl-examples-web
   ```

Ship a launcher (a small script that serves the `.js` / `.wasm` pair —
with COOP/COEP when threaded) inside the bundle's `bin/`; that is the
`cvcgl-examples-web` pattern, and it makes the bundle self-contained.

## Gotchas

1. **Mixing `wasm` and `wasm-mt`** → `--shared-memory is disallowed`. One
   platform per build, end to end (deps *and* app objects).
2. **Threaded deps, unthreaded app objects** → the same error, at link.
   `cvcpkg_wasm_threads()` before the targets are created.
3. **`wasm-mt` without COOP/COEP** → `SharedArrayBuffer` is undefined; the
   app dies at startup with no useful error.
4. **Fixed-heap OOM in the browser only** → memory growth off; the
   boilerplate's `MEMORY_GROWTH` default is `ON` for exactly this.
5. **ASan builds**: `-fsanitize=address` and mimalloc do not combine;
   `MIMALLOC AUTO` handles it (OFF under ASan). ASan also bloats the
   binary and is slow — treat it as debug-only.
6. **`yaml-cpp` has no wasm catalog entry** (linux/macos/win/bsd only).
   In-tree, the demos build it from source into the deps prefix
   (`build-wasm-demo.sh`); a catalog consumer that needs it must do the
   same.
7. **Don't `--with-deps` from source for a wasm closure.** It rebuilds the
   whole closure — including host tools — from source; the publish
   workflow avoids it deliberately. Install the catalog bundles instead.
8. **Emscripten cache on shared builders**: `emsdk_env.sh` clears
   `EM_CACHE`; if the emsdk prefix belongs to another user, point
   `EM_CACHE` at a writable dir (the note in `cvc_wasm_run.sh` describes
   exactly this failure mode).

## Where things live

| What | Where |
| --- | --- |
| this guide (general) | `cvcpkg/docs/wasm-packaging.md` |
| CMake boilerplate | `cvcpkg/cmake/cvcpkg-wasm-app.cmake` |
| requirements-file examples | `cvcpkg/examples/*-requirements.yaml` |
| cvcGL app contract (shim, FrameYield, mimalloc, glsync, serve.py) | `libcvc/docs/CVCGL_WASM.md` |
| full build + package flows with `/tmp` prefixes | `libcvc/docs/FULL_BUILD_CVCPKG.md` |
| CI reference (build + pack + publish the cvcGL wasm-mt bundles) | `libcvc/.github/workflows/publish-cvcgl-wasm.yml` |
| in-tree gallery dev loop (9 demos + `ariadne_hello`) | `libcvc/src/cvcGL/examples/wasm/build-wasm-demo.sh` |
| env scripts behind the recipes | `cvcpkg/recipes/_common/env-wasm.sh`, `env-wasm-mt.sh`, `env-wasi.sh`, `cvc_wasm_run.sh` |
