#!/usr/bin/env bash
# recipes/levmar/build-wasi.sh — cross-compile levmar to wasm32-wasi via wasi-sdk.
# Uses CLAPACK (wasi build) for BLAS/LAPACK on wasi.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_cmake_build \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}" \
    -DUSE_BLAS=ON
