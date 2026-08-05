#!/usr/bin/env bash
# recipes/libogg/build-wasi.sh — cross-compile libogg to wasm32-wasi via wasi-sdk.
#
# Nothing in libogg touches the wasip1 gaps: no threads, no sockets, no dlopen,
# no fork, and no filesystem — the library only ever works on buffers handed to
# it by the caller.  Its one platform dependency is fixed-width integer types,
# which configure derives from the compiler, so the single-threaded sysroot is
# sufficient.  Modelled on recipes/iconv/build-wasi.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cd "${CVC_SOURCE_DIR}"

CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")
export CC_FOR_BUILD

WASI_TARGET_FLAGS="--target=wasm32-wasip1 --sysroot=${CVC_WASI_SDK_DIR}/share/wasi-sysroot"
export CFLAGS="${WASI_TARGET_FLAGS} ${CFLAGS:-}"
export LDFLAGS="${WASI_TARGET_FLAGS} ${LDFLAGS:-}"

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=wasm32-wasi \
    --build="${BUILD_TRIPLET}" \
    --disable-shared \
    --enable-static \
    --disable-dependency-tracking

make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
