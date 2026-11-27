#!/usr/bin/env bash
# recipes/libpng/build-wasi.sh — cross-compile libpng to wasm32-wasi via wasi-sdk.
#
# libpng needs nothing from the platform that wasi-libc lacks: stdio for the
# optional FILE-based readers, setjmp/longjmp for its error handling (wasi-sdk
# supports these), and zlib, which already builds for wasi.  As on wasm we
# force the static-only target set and drop the SIMD intrinsics — see
# build-wasm.sh for the reasoning.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

# libpng names its archives the Unix way (libpng16.a, which libpng16.pc's
# -lpng16 and FindPNG expect) only `if(UNIX OR <a Windows toolchain with a lib
# prefix>)`.  CMake's WASI platform sets WASI, not UNIX, so the archive came
# out as the MSVC spelling liblibpng16_static.a.  Count WASI as Unix there.
_png_cmake="${CVC_SOURCE_DIR}/CMakeLists.txt"
if [[ "$(grep -c '^if(UNIX$' "${_png_cmake}")" != 1 ]]; then
    echo "libpng: expected one 'if(UNIX' line in CMakeLists.txt to patch" >&2
    exit 1
fi
sed -i 's/^if(UNIX$/if(UNIX OR WASI/' "${_png_cmake}"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX:-}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cvc_cmake_build \
    -DPNG_TESTS=OFF \
    -DPNG_TOOLS=OFF \
    -DPNG_SHARED=OFF \
    -DPNG_STATIC=ON \
    -DPNG_HARDWARE_OPTIMIZATIONS=OFF
