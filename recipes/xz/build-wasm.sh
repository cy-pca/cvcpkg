#!/usr/bin/env bash
# recipes/xz/build-wasm.sh — cross-compile xz/liblzma to wasm via Emscripten.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF
