#!/usr/bin/env bash
# recipes/tiff/build-wasm.sh — cross-compile libtiff to wasm.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -Dtiff-tests=OFF \
    -Dtiff-tools=OFF \
    -Dtiff-contrib=OFF \
    -Dtiff-docs=OFF \
    -Djbig=OFF \
    -Dlibdeflate=OFF
