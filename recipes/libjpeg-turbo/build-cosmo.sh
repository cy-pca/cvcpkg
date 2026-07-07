#!/usr/bin/env bash
# recipes/libjpeg-turbo/build-cosmo.sh — cross-compile libjpeg-turbo with Cosmopolitan.
# SIMD/ASM is disabled (cosmocc doesn't support NASM asm).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cvc_cmake_build \
    -DENABLE_SHARED=OFF \
    -DENABLE_STATIC=ON \
    -DWITH_TURBOJPEG=ON \
    -DWITH_JAVA=OFF \
    -DWITH_SIMD=OFF \
    -DREQUIRE_SIMD=OFF
