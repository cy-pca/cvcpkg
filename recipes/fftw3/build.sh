#!/usr/bin/env bash
# recipes/fftw3/build.sh — build FFTW3 double + single + threads.
# Two cmake builds: double precision, then single precision (float).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

COMMON_ARGS=(
    -DBUILD_TESTS=OFF
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
    -DENABLE_THREADS=ON
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5
)

# Pass 1: double precision
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}/double" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS="${BUILD_SHARED_LIBS}" \
    "${COMMON_ARGS[@]}"
cmake --build "${CVC_BUILD_DIR}/double" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}/double"

# Pass 2: single precision (float)
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}/float" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS="${BUILD_SHARED_LIBS}" \
    -DENABLE_FLOAT=ON \
    "${COMMON_ARGS[@]}"
cmake --build "${CVC_BUILD_DIR}/float" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}/float"

# Normalize .pc / .cmake to use ${pcfiledir} / ${CMAKE_CURRENT_LIST_DIR}
# so downstream consumers work in any prefix.
cvc_rewrite_install_paths
