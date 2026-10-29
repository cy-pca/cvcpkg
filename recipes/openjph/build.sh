#!/usr/bin/env bash
# recipes/openjph/build.sh — build the OpenJPH library (no executables).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DOJPH_BUILD_TESTS=OFF \
    -DOJPH_BUILD_EXECUTABLES=OFF \
    -DOJPH_ENABLE_TIFF_SUPPORT=OFF
