#!/usr/bin/env bash
# recipes/log4cplus/build-wasi.sh — cross-compile log4cplus to wasm32-wasi via wasi-sdk.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_cmake_build \
    -DLOG4CPLUS_BUILD_TESTING=OFF \
    -DLOG4CPLUS_BUILD_LOGGINGSERVER=OFF \
    -DWITH_UNIT_TESTS=OFF
