#!/usr/bin/env bash
# recipes/iconv/build-wasm.sh — cross-compile GNU libiconv to wasm via Emscripten.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")
export CC_FOR_BUILD

emconfigure ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=wasm32-unknown-emscripten \
    --build="${BUILD_TRIPLET}" \
    --disable-shared \
    --enable-static \
    --disable-dependency-tracking

emmake make -j "${CVC_JOBS}"
emmake make install

cvc_rewrite_install_paths
