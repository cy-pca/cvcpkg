#!/usr/bin/env bash
# recipes/wasmedge/build.sh — build WasmEdge from source on Linux/macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DWASMEDGE_BUILD_TESTS=OFF \
    -DWASMEDGE_BUILD_TOOLS=ON \
    -DWASMEDGE_BUILD_PLUGINS=OFF \
    -DWASMEDGE_BUILD_SHARED_LIB=ON \
    -DWASMEDGE_BUILD_STATIC_LIB=ON
