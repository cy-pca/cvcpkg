#!/usr/bin/env bash
# recipes/cgal/build-wasi.sh — cross-compile CGAL to wasm32-wasi via wasi-sdk.
#
# CGAL is largely header-only.  The precompiled libCGAL_Core needs GMP +
# MPFR (both available as wasi builds), but the Boost dependency is only
# header-includes at consumer compile-time (no linkage), so we run in
# header-only mode to avoid needing boost's wasi libraries built.
# ImageIO and Qt6 are disabled for wasi.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_cmake_build \
    -DCGAL_HEADER_ONLY=ON \
    -DWITH_CGAL_Core=OFF \
    -DWITH_CGAL_ImageIO=OFF \
    -DWITH_CGAL_Qt6=OFF \
    -DWITH_examples=OFF \
    -DWITH_demos=OFF \
    -DWITH_tests=OFF
