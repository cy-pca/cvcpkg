#!/usr/bin/env bash
# recipes/libunistring/build-wasm.sh — cross-compile libunistring to wasm.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")
export CC_FOR_BUILD

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --host=wasm32-unknown-emscripten
    --build="${BUILD_TRIPLET}"
    --disable-shared
    --enable-static
    --disable-dependency-tracking
)
if [[ -d "${CVC_DEPS_PREFIX:-}/include" ]]; then
    CONFIGURE_ARGS+=(--with-libiconv-prefix="${CVC_DEPS_PREFIX}")
fi

emconfigure ./configure "${CONFIGURE_ARGS[@]}"
emmake make -j "${CVC_JOBS}"
emmake make install

cvc_rewrite_install_paths
