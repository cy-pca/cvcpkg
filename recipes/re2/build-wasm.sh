#!/usr/bin/env bash
# recipes/re2/build-wasm.sh — cross-compile RE2 to wasm.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -DRE2_BUILD_TESTING=OFF \
    -DCMAKE_CXX_STANDARD=17
