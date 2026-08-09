#!/usr/bin/env bash
# recipes/zstd/build-wasi.sh — cross-compile zstd to wasm32-wasi via wasi-sdk.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

# zstd's CMakeLists.txt is in build/cmake/.
CVC_SOURCE_DIR="${CVC_SOURCE_DIR}/build/cmake"

cvc_cmake_build \
    -DZSTD_BUILD_PROGRAMS=OFF \
    -DZSTD_BUILD_CONTRIB=OFF \
    -DZSTD_BUILD_TESTS=OFF \
    -DZSTD_BUILD_STATIC=ON \
    -DZSTD_BUILD_SHARED=OFF
