#!/usr/bin/env bash
# recipes/nfft3/build-wasi.sh — cross-compile NFFT3 to wasm32-wasi via wasi-sdk.
# Depends on fftw3 (wasi build), threads/OpenMP disabled.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cd "${CVC_SOURCE_DIR}"

if [[ -n "${CVC_DEPS_PREFIX:-}" && -d "${CVC_DEPS_PREFIX}/include" ]]; then
    FFTW_PREFIX="${CVC_DEPS_PREFIX}"
else
    FFTW_PREFIX="${CVC_INSTALL_DIR}"
fi

CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")
export CC_FOR_BUILD

WASI_TARGET_FLAGS="--target=wasm32-wasip1 --sysroot=${_WASI_SYSROOT}"
export CFLAGS="${WASI_TARGET_FLAGS} -O3 ${CFLAGS:-}"
export LDFLAGS="${WASI_TARGET_FLAGS} ${LDFLAGS:-}"

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=wasm32-wasi \
    --build="${BUILD_TRIPLET}" \
    --disable-shared \
    --enable-static \
    --with-pic \
    --disable-examples \
    --disable-applications \
    --disable-openmp \
    --with-fftw3-includedir="${FFTW_PREFIX}/include" \
    --with-fftw3-libdir="${FFTW_PREFIX}/lib"

make -j "${CVC_JOBS}"
make install
