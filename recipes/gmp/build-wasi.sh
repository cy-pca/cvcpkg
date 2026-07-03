#!/usr/bin/env bash
# recipes/gmp/build-wasi.sh — cross-compile GMP to wasm32-wasi via wasi-sdk.
#
# wasi-sdk is a clang toolchain, so no emconfigure wrapper is needed.
# env-wasi.sh already exported CC/CXX/AR/RANLIB/NM/STRIP pointing at
# wasi-sdk binaries; we just tell autoconf that we're cross-compiling
# via --host=wasm32-wasi and pass the sysroot through CFLAGS/LDFLAGS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cd "${CVC_SOURCE_DIR}"

# GMP's configure needs CC_FOR_BUILD (a native compiler for its host-side
# code generators like gen-fac).
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
    --enable-cxx \
    --disable-assembly

make -j "${CVC_JOBS}"
make install

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
