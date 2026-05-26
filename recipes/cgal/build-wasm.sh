#!/usr/bin/env bash
# recipes/cgal/build-wasm.sh — cross-compile CGAL to wasm.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -DCGAL_HEADER_ONLY=OFF \
    -DWITH_CGAL_Core=ON \
    -DWITH_CGAL_ImageIO=OFF \
    -DWITH_CGAL_Qt6=OFF
