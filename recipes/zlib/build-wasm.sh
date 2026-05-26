#!/usr/bin/env bash
# recipes/zlib/build-wasm.sh — cross-compile zlib to wasm via Emscripten.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -DZLIB_BUILD_EXAMPLES=OFF \
    -DINSTALL_PKGCONFIG_DIR="${CVC_INSTALL_DIR}/lib/pkgconfig"
