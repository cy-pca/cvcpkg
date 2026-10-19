#!/usr/bin/env bash
# recipes/ftxui/build-wasm.sh — cross-compile FTXUI to wasm via Emscripten.
#
# FTXUI supports Emscripten upstream: on __EMSCRIPTEN__ its App reads stdin and
# writes stdout (a JS terminal such as xterm.js sits on the other end, fed through
# FS.init) and exports ftxui_on_resize(cols, rows) for the page to report the
# terminal size. Input is polled inside App::RunOnce, so FTXUI 7 starts no thread
# of its own. That is what lets the Ariadne terminal backend run in the browser
# (libcvc docs/roadmap/CVCGL-UI-DSL-ROADMAP.md §16.4).
#
# One upstream flag has to go. cmake/ftxui_set_options.cmake appends
# "-s USE_PTHREADS" to CMAKE_CXX_FLAGS on every Emscripten build (and
# PROXY_TO_PTHREAD to the exe link flags, for its own examples). Emscripten
# refuses to link -pthread objects into a module built without it, so that line
# would make the plain `wasm` bundle unusable by the single-threaded consumers
# this platform exists for (the gh-pages demo gallery has no COOP/COEP). Drop it
# here; threading is env-wasm.sh's call: the `wasm-mt` matrix entry sets
# CVC_WASM_THREADS=1, which compiles the whole closure with -pthread.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

# Portable sed -i: macOS BSD sed requires '' as backup extension arg.
if [[ "$(uname)" == "Darwin" ]]; then
    _sed_i() { sed -i '' "$@"; }
else
    _sed_i() { sed -i "$@"; }
fi

_opts="${CVC_SOURCE_DIR}/cmake/ftxui_set_options.cmake"
_sed_i '/string(APPEND CMAKE_CXX_FLAGS " -s USE_PTHREADS")/d' "${_opts}"
_sed_i '/string(APPEND CMAKE_EXE_LINKER_FLAGS " -s PROXY_TO_PTHREAD/d' "${_opts}"
# Fail loudly if upstream moved the flag: a silent no-op would ship -pthread
# objects in the single-threaded bundle.
if grep -q 'USE_PTHREADS' "${_opts}"; then
    echo "ftxui build-wasm.sh: ${_opts} still forces USE_PTHREADS; update the sed above" >&2
    exit 1
fi

cvc_cmake_build \
    -DFTXUI_BUILD_EXAMPLES=OFF \
    -DFTXUI_BUILD_TESTS=OFF \
    -DFTXUI_BUILD_DOCS=OFF \
    -DFTXUI_BUILD_MODULES=OFF \
    -DFTXUI_ENABLE_INSTALL=ON \
    -DFTXUI_QUIET=ON
