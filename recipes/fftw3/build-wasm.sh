#!/usr/bin/env bash
# recipes/fftw3/build-wasm.sh — cross-compile FFTW3 to wasm.
# Two cmake passes: double precision, then single precision (float).
# Threading is disabled for wasm.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

COMMON_ARGS=(
    -DBUILD_TESTS=OFF
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
    -DENABLE_THREADS=OFF
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5
    -DCMAKE_TOOLCHAIN_FILE="${EMSDK}/upstream/emscripten/cmake/Modules/Platform/Emscripten.cmake"
)

# Pass 1: double precision
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}/double" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS=OFF \
    "${COMMON_ARGS[@]}"
cmake --build "${CVC_BUILD_DIR}/double" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}/double"

# Pass 2: single precision (float)
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}/float" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS=OFF \
    -DENABLE_FLOAT=ON \
    "${COMMON_ARGS[@]}"
cmake --build "${CVC_BUILD_DIR}/float" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}/float"
