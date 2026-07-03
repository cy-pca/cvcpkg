#!/usr/bin/env bash
# recipes/hdf5/build-wasi.sh — cross-compile HDF5 to wasm32-wasi via wasi-sdk.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

# wasi-sdk's fenv.h likewise doesn't define FE_INVALID/FE_DIVBYZERO/FE_OVERFLOW.
HDF5_C_FLAGS="-DFE_INVALID=0 -DFE_DIVBYZERO=0 -DFE_OVERFLOW=0"

cvc_cmake_build \
    -DHDF5_BUILD_CPP_LIB=ON \
    -DHDF5_BUILD_TOOLS=OFF \
    -DHDF5_BUILD_EXAMPLES=OFF \
    -DBUILD_TESTING=OFF \
    -DHDF5_ENABLE_Z_LIB_SUPPORT=ON \
    -DHDF5_ENABLE_THREADSAFE=OFF \
    -DCMAKE_C_FLAGS="${HDF5_C_FLAGS}"
