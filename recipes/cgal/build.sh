#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# CGAL is mostly header-only; this installs the headers + CMake config.
cvc_cmake_build \
    -DCGAL_HEADER_ONLY=OFF \
    -DWITH_CGAL_Core=ON \
    -DWITH_CGAL_ImageIO=OFF \
    -DWITH_CGAL_Qt6=OFF
