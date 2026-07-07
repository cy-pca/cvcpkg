#!/usr/bin/env bash
# recipes/log4cplus/build-cosmo.sh — cross-compile log4cplus with Cosmopolitan.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cvc_cmake_build \
    -DLOG4CPLUS_BUILD_TESTING=OFF \
    -DLOG4CPLUS_BUILD_LOGGINGSERVER=OFF \
    -DWITH_UNIT_TESTS=OFF
