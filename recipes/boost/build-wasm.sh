#!/usr/bin/env bash
# recipes/boost/build-wasm.sh — cross-compile Boost to wasm.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -DBOOST_ENABLE_CMAKE=ON \
    -DBUILD_TESTING=OFF \
    -DBOOST_INSTALL_LAYOUT=system \
    -DCMAKE_CXX_FLAGS="-DBOOST_HAS_PTHREADS" \
    -DBOOST_EXCLUDE_LIBRARIES="context;coroutine;fiber;stacktrace"
