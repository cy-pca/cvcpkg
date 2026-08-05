#!/usr/bin/env bash
# recipes/libpng/build-wasi.sh — cross-compile libpng to wasm32-wasi via wasi-sdk.
#
# libpng needs nothing from the platform that wasi-libc lacks: stdio for the
# optional FILE-based readers, setjmp/longjmp for its error handling (wasi-sdk
# supports these), and zlib, which already builds for wasi.  As on wasm we
# force the static-only target set and drop the SIMD intrinsics — see
# build-wasm.sh for the reasoning.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX:-}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cvc_cmake_build \
    -DPNG_TESTS=OFF \
    -DPNG_TOOLS=OFF \
    -DPNG_SHARED=OFF \
    -DPNG_STATIC=ON \
    -DPNG_HARDWARE_OPTIMIZATIONS=OFF
