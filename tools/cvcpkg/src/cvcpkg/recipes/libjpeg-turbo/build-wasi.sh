#!/usr/bin/env bash
# recipes/libjpeg-turbo/build-wasi.sh — cross-compile libjpeg-turbo to wasm32-wasi.
# SIMD/ASM is disabled (no NASM for wasm32).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_cmake_build \
    -DENABLE_SHARED=OFF \
    -DENABLE_STATIC=ON \
    -DWITH_TURBOJPEG=ON \
    -DWITH_JAVA=OFF \
    -DWITH_SIMD=OFF \
    -DREQUIRE_SIMD=OFF
