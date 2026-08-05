#!/usr/bin/env bash
# recipes/libpng/build-wasm.sh — cross-compile libpng to wasm via Emscripten.
#
# libpng is plain C89 over zlib with no OS dependencies beyond stdio and
# setjmp, both of which Emscripten provides, and zlib already covers wasm.
# Two things differ from the native build:
#   * PNG_SHARED=OFF — libpng's shared/static targets are independent options
#     (not BUILD_SHARED_LIBS), and on a static-only target CMake would demote
#     the shared one to static and collide on the same archive name.
#   * PNG_HARDWARE_OPTIMIZATIONS=OFF — the intrinsics are x86 SSE / ARM NEON /
#     POWER VSX / MIPS MSA; none exist for wasm, so take the portable C path.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX:-}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cvc_cmake_build \
    -DPNG_TESTS=OFF \
    -DPNG_TOOLS=OFF \
    -DPNG_SHARED=OFF \
    -DPNG_STATIC=ON \
    -DPNG_HARDWARE_OPTIMIZATIONS=OFF
