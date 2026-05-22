#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Boost's CMake build (>= 1.82) supports standard CMake workflow.
cvc_cmake_build \
    -DBOOST_ENABLE_CMAKE=ON \
    -DBUILD_TESTING=OFF \
    -DBOOST_INSTALL_LAYOUT=system
