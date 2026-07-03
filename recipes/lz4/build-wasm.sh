#!/usr/bin/env bash
# recipes/lz4/build-wasm.sh — cross-compile lz4 to wasm via Emscripten.
# lz4's cmake project lives under build/cmake/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

CVC_SOURCE_DIR="${CVC_SOURCE_DIR}/build/cmake" cvc_cmake_build \
    -DLZ4_BUILD_CLI=OFF \
    -DLZ4_BUILD_LEGACY_LZ4C=OFF
