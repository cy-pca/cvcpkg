#!/usr/bin/env bash
# recipes/libffi/build-wasm.sh — cross-compile libffi to wasm via Emscripten.
#
# Upstream libffi has supported wasm32/Emscripten since 3.4.5; 3.7.1's
# configure.host maps `wasm32-*-*` to TARGET=wasm32 / TARGETDIR=wasm with
# SOURCES="ffi.c" — a pure-C backend, no assembly trampolines.  It works by
# generating JS thunks at run time (src/wasm/ffi.c is built on EM_JS and
# Emscripten's wasm table helpers), which is why Emscripten is the only
# wasm flavour libffi supports; see the recipe's note for why there is no
# wasi entry.
#
# libffi is autotools, so emconfigure/emmake wrap ./configure and make.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

# Explicit --build keeps autoconf in cross mode (it must not try to run the
# wasm test programs it compiles).
CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")
export CC_FOR_BUILD

emconfigure ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=wasm32-unknown-emscripten \
    --build="${BUILD_TRIPLET}" \
    --disable-shared \
    --enable-static \
    --disable-docs \
    --disable-multi-os-directory \
    --includedir="${CVC_INSTALL_DIR}/include"

emmake make -j "${CVC_JOBS}"
emmake make install

cvc_rewrite_install_paths
