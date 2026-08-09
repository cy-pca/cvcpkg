#!/usr/bin/env bash
# recipes/libopus/build-wasm.sh — cross-compile libopus to wasm via Emscripten.
#
# Opus is a self-contained codec: buffers in, buffers out, no files, no threads,
# no sockets.  The only thing standing between it and a cross target is its
# architecture-specific code, and opus exposes exactly the three switches needed
# to turn that off:
#   --disable-asm         — drops the hand-written ARM/x86 assembly.
#   --disable-rtcd        — drops runtime CPU dispatch, which on x86 reaches for
#                           `cpuid` and on ARM for getauxval/AT_HWCAP.  Neither
#                           concept exists in a wasm module, and the dispatch
#                           table would be selecting between variants we are not
#                           building anyway.
#   --disable-intrinsics  — drops the SSE/SSE2/SSE4.1/AVX2 and NEON intrinsic
#                           paths; wasm SIMD is a different instruction set and
#                           opus has no wasm128 backend.
# What is left is the reference C, which is the configuration Emscripten's own
# opus port ships.
#
# --enable-custom-modes is carried over from the native build.sh: opus_custom.h
# is installed either way, so consumers that probe only the header enable the
# code path and then fail to link without these symbols.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")
export CC_FOR_BUILD

emconfigure ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=wasm32-unknown-emscripten \
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

emmake make -j "${CVC_JOBS}"
emmake make install

cvc_rewrite_install_paths
