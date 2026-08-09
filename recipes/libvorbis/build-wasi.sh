#!/usr/bin/env bash
# recipes/libvorbis/build-wasi.sh — cross-compile libvorbis to wasm32-wasi.
#
# The codec itself is self-contained floating-point DSP — no threads, sockets,
# dlopen or fork, so none of the wasip1 gaps apply.  libvorbisfile's
# ov_open(FILE *) needs stdio, which wasi-libc provides (a wasip1 guest reaches
# real files only through preopened directories, but that is the embedder's
# concern at run time, not a build-time dependency); ov_open_callbacks lets a
# consumer bypass stdio entirely.  libogg already covers wasi, satisfying the
# runtime dependency this entry needs for closure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cd "${CVC_SOURCE_DIR}"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX:-}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

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
    --with-ogg="${CVC_DEPS_PREFIX}" \
    --disable-shared \
    --enable-static \
    --disable-dependency-tracking

make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
