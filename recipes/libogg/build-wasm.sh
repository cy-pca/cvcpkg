#!/usr/bin/env bash
# recipes/libogg/build-wasm.sh — cross-compile libogg to wasm via Emscripten.
#
# libogg is the Ogg framing layer and nothing else: bitstream packing, CRC and
# page assembly over buffers the caller owns.  It opens no files, starts no
# threads and makes no syscalls; the only platform question it asks is how wide
# the integer types are, and configure answers that from the compiler rather
# than by running a probe.  That is what makes it a clean cross target.
#
# Autotools shape rather than cvc_cmake_build: libogg ships both a configure
# script and a CMakeLists.txt, and the native build.sh uses configure.  Staying
# on the same build system keeps the installed layout byte-for-byte the same
# shape as every other platform, which is what package.files globs against —
# the CMake path additionally emits lib/cmake/Ogg/, which the package does not
# declare.  Modelled on recipes/iconv/build-wasm.sh.
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
    --disable-dependency-tracking

emmake make -j "${CVC_JOBS}"
emmake make install

cvc_rewrite_install_paths
