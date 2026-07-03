#!/usr/bin/env bash
# recipes/tiff/build-wasi.sh — cross-compile libtiff to wasm32-wasi via wasi-sdk.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_cmake_build \
    -Dtiff-tests=OFF \
    -Dtiff-tools=OFF \
    -Dtiff-contrib=OFF \
    -Dtiff-docs=OFF \
    -Djbig=OFF \
    -Dlibdeflate=OFF
