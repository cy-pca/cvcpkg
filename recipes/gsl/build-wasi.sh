#!/usr/bin/env bash
# recipes/gsl/build-wasi.sh — cross-compile GSL to wasm32-wasi via wasi-sdk.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cd "${CVC_SOURCE_DIR}"

CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")
export CC_FOR_BUILD

WASI_TARGET_FLAGS="--target=wasm32-wasip1 --sysroot=${_WASI_SYSROOT}"
export CFLAGS="${WASI_TARGET_FLAGS} ${CFLAGS:-}"
export CXXFLAGS="${WASI_TARGET_FLAGS} ${CXXFLAGS:-}"
export LDFLAGS="${WASI_TARGET_FLAGS} ${LDFLAGS:-}"

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=wasm32-wasi \
    --build="${BUILD_TRIPLET}" \
    --disable-shared \
    --enable-static \
    --with-pic

make -j "${CVC_JOBS}"
make install

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
