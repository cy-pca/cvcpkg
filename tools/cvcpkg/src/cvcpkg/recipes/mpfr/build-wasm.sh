#!/usr/bin/env bash
# recipes/mpfr/build-wasm.sh — cross-compile MPFR to wasm via Emscripten.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

emconfigure ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=none-none-none \
    --disable-shared \
    --enable-static \
    --with-gmp="${CVC_DEPS_PREFIX}"

emmake make -j "${CVC_JOBS}"
emmake make install
