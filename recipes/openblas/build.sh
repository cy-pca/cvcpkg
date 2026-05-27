#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DUSE_OPENMP=ON \
    -DNO_LAPACKE=OFF \
    -DDYNAMIC_ARCH=ON \
    -DTARGET=GENERIC
