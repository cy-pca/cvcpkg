#!/usr/bin/env bash
# recipes/libspatialindex/build-wasi.sh — cross-compile libspatialindex to wasm32-wasi via wasi-sdk.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF
