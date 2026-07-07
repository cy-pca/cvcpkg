#!/usr/bin/env bash
# recipes/lz4/build-cosmo.sh — cross-compile lz4 with Cosmopolitan.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

CVC_SOURCE_DIR="${CVC_SOURCE_DIR}/build/cmake" cvc_cmake_build \
    -DLZ4_BUILD_CLI=OFF \
    -DLZ4_BUILD_LEGACY_LZ4C=OFF
