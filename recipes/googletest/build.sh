#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DBUILD_GMOCK=ON \
    -DINSTALL_GTEST=ON \
    -DCMAKE_CXX_STANDARD=17
