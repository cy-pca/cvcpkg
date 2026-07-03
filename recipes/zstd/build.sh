#!/usr/bin/env bash
# zstd's cmake project lives under build/cmake/ in the source tree.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}/build/cmake" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS="${BUILD_SHARED_LIBS}" \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_CXX_STANDARD=17 \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DZSTD_BUILD_PROGRAMS=OFF \
    -DZSTD_BUILD_CONTRIB=OFF \
    -DZSTD_BUILD_TESTS=OFF \
    -DZSTD_BUILD_STATIC="$(if [ "$BUILD_SHARED_LIBS" = "OFF" ]; then echo ON; else echo OFF; fi)" \
    -DZSTD_BUILD_SHARED="${BUILD_SHARED_LIBS}"
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
