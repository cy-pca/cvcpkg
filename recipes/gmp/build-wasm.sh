#!/usr/bin/env bash
# recipes/gmp/build-wasm.sh — cross-compile GMP to wasm via Emscripten.
# GMP uses autotools; we use emconfigure to wrap ./configure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

# GMP cross-compilation needs CC_FOR_BUILD set to a native C compiler
# (for building host-side code generators like gen-fac). emconfigure sets
# CC=emcc but CC_FOR_BUILD must be the real host compiler.
# Also specify --build explicitly so configure properly detects cross-compilation.
export CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")

# GMP's configure needs the host triplet for cross-compilation.
emconfigure ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=none-none-none \
    --build="${BUILD_TRIPLET}" \
    --disable-shared \
    --enable-static \
    --enable-cxx \
    --disable-assembly

emmake make -j "${CVC_JOBS}"
emmake make install

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
