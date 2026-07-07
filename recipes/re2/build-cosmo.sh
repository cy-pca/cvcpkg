#!/usr/bin/env bash
# recipes/re2/build-cosmo.sh — cross-compile RE2 with Cosmopolitan.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cvc_cmake_build \
    -DRE2_BUILD_TESTING=OFF \
    -DCMAKE_CXX_STANDARD=17
