#!/usr/bin/env bash
# recipes/libjpeg-turbo/build-wasm.sh — cross-compile libjpeg-turbo to wasm.
# Note: SIMD/ASM is disabled for wasm (no NASM).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -DENABLE_SHARED=OFF \
    -DENABLE_STATIC=ON \
    -DWITH_TURBOJPEG=ON \
    -DWITH_JAVA=OFF \
    -DWITH_SIMD=OFF \
    -DREQUIRE_SIMD=OFF
