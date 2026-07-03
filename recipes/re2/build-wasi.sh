#!/usr/bin/env bash
# recipes/re2/build-wasi.sh — cross-compile RE2 to wasm32-wasi via wasi-sdk.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_cmake_build \
    -DRE2_BUILD_TESTING=OFF \
    -DCMAKE_CXX_STANDARD=17
