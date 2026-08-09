#!/usr/bin/env bash
# recipes/libopus/build-wasi.sh — cross-compile libopus to wasm32-wasi.
#
# The codec needs nothing that wasip1 lacks: it is pure computation over
# caller-owned buffers, with no threads, sockets, dlopen or fork, and it does
# not even need stdio (only the extra programs, which stay disabled, do).
#
# asm/rtcd/intrinsics are all disabled for the same reason as the Emscripten
# build: the assembly and intrinsic paths are x86/ARM-only, and runtime CPU
# dispatch has nothing to dispatch on in a wasm module — `cpuid` and
# getauxval(AT_HWCAP) have no wasm equivalent.  The reference C is what remains.
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
    --disable-dependency-tracking \
    --disable-extra-programs \
    --disable-doc \
    --enable-custom-modes \
    --disable-asm \
    --disable-rtcd \
    --disable-intrinsics

make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
