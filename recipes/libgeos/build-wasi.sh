#!/usr/bin/env bash
# recipes/libgeos/build-wasi.sh — cross-compile GEOS to wasm32-wasi via wasi-sdk.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DBUILD_BENCHMARKS=OFF
