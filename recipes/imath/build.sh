#!/usr/bin/env bash
# recipes/imath/build.sh — build Imath (C++ only; no Python bindings).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DPYTHON=OFF \
    -DIMATH_INSTALL_PKG_CONFIG=ON
