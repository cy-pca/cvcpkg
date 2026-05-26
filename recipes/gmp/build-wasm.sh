#!/usr/bin/env bash
# recipes/gmp/build-wasm.sh — cross-compile GMP to wasm via Emscripten.
# GMP uses autotools; we use emconfigure to wrap ./configure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

# GMP's configure needs the host triplet for cross-compilation.
emconfigure ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=none-none-none \
    --disable-shared \
    --enable-static \
    --enable-cxx \
    --disable-assembly

emmake make -j "${CVC_JOBS}"
emmake make install
