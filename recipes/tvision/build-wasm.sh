#!/usr/bin/env bash
# recipes/tvision/build-wasm.sh — cross-compile tvision to wasm via Emscripten, for a page that
# runs a tvision program behind a JS terminal (xterm.js): libcvc's `ari_tvision` web page, the
# Ariadne tvision backend in the browser.
#
# Upstream tvision has no browser console: on Unix it needs ncursesw for input and display, and it
# waits for input in select(). emscripten-browser-console.patch (applied to every column; it only
# compiles under __EMSCRIPTEN__) adds one:
#   - output is plain ANSI through tvision's own AnsiScreenWriter on stdout, each flush ended with a
#     NUL (the page writes its terminal once per frame, as with FTXUI's Emscripten screen);
#   - input is stdin, read without blocking (the page feeds xterm.js's bytes through
#     Module.stdin), decoded by tvision's TermIO (keys, SGR mouse, bracketed paste);
#   - the page reports its size through the exported tvision_set_screen_size(cols, rows);
#   - the event wait yields to the page with emscripten_sleep, so a program linking this needs
#     -sASYNCIFY (libcvc's ari pages have it).
# tvision starts no threads, so the single-threaded `wasm` bundle is the one a static page uses;
# the `wasm-mt` entry builds the same with -pthread (env-wasm.sh's CVC_WASM_THREADS hook).
#
# No ncurses and no GPM here. Upstream's install target exports a relocatable CMake config
# (tvision::tvision, no absolute paths: nothing to link but the archive), which libcvc's static
# wasm build finds with find_package(tvision CONFIG).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -DTV_BUILD_EXAMPLES=OFF \
    -DTV_BUILD_TESTS=OFF \
    -DTV_OPTIMIZE_BUILD=OFF

# The browser console must be in the archive: check for the function the page calls.
"${EMSDK}/upstream/bin/llvm-nm" "${CVC_INSTALL_DIR}/lib/libtvision.a" 2>/dev/null \
    | grep ' T tvision_set_screen_size$' >/dev/null \
    || { echo "tvision build-wasm.sh: no browser console in libtvision.a (emscripten-browser-console.patch not applied?)" >&2; exit 1; }
test -f "${CVC_INSTALL_DIR}/lib/cmake/tvision/tvision-config.cmake" \
    || { echo "tvision build-wasm.sh: no CMake config installed" >&2; exit 1; }
