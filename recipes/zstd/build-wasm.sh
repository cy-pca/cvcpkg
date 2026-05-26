#!/usr/bin/env bash
# recipes/zstd/build-wasm.sh — cross-compile zstd to wasm via Emscripten.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}/build/cmake" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_CXX_STANDARD=17 \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_TOOLCHAIN_FILE="${EMSDK}/upstream/emscripten/cmake/Modules/Platform/Emscripten.cmake" \
    -DZSTD_BUILD_PROGRAMS=OFF \
    -DZSTD_BUILD_CONTRIB=OFF \
    -DZSTD_BUILD_TESTS=OFF \
    -DZSTD_BUILD_STATIC=ON \
    -DZSTD_BUILD_SHARED=OFF
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"
