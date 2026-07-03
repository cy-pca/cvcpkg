#!/usr/bin/env bash
# recipes/log4cplus/build.sh — build log4cplus from source.
# Two passes on Linux: shared first, then static into the same prefix.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

COMMON_ARGS=(
    -DLOG4CPLUS_BUILD_TESTING=OFF
    -DLOG4CPLUS_BUILD_LOGGINGSERVER=OFF
    -DWITH_UNIT_TESTS=OFF
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
)

# Pass 1: shared
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}/shared" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS=ON \
    "${COMMON_ARGS[@]}"
cmake --build "${CVC_BUILD_DIR}/shared" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}/shared"

# Pass 2: static (adds .a alongside .so)
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}/static" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS=OFF \
    "${COMMON_ARGS[@]}"
cmake --build "${CVC_BUILD_DIR}/static" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}/static"

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
