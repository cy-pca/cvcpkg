#!/usr/bin/env bash
# recipes/gsl/build-wasm.sh — cross-compile GSL to wasm via Emscripten.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

emconfigure ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=none-none-none \
    --disable-shared \
    --enable-static \
    --with-pic

emmake make -j "${CVC_JOBS}"
emmake make install
