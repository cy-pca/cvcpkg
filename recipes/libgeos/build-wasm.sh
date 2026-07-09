#!/usr/bin/env bash
# recipes/libgeos/build-wasm.sh — cross-compile GEOS to wasm via Emscripten.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DBUILD_BENCHMARKS=OFF
