#!/usr/bin/env bash
# recipes/miniupnpc/build.sh — CMake-based build of MiniUPnPc.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DUPNPC_BUILD_STATIC=$([ "${CVC_LINK:-shared}" = static ] && echo ON || echo OFF) \
    -DUPNPC_BUILD_SHARED=$([ "${CVC_LINK:-shared}" = static ] && echo OFF || echo ON) \
    -DUPNPC_BUILD_TESTS=OFF \
    -DUPNPC_BUILD_SAMPLE=ON
