#!/usr/bin/env bash
# recipes/freetype/build-wasi.sh — cross-compile FreeType to wasm32-wasi.
#
# FreeType's CMake build uses the ANSI-C ftsystem.c (stdio + malloc only), so
# nothing here touches the wasip1 gaps — no threads, signals, sockets or
# dlopen are involved.  zlib, libpng and bzip2 all cover wasi already.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX:-}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cvc_cmake_build \
    -DFT_REQUIRE_ZLIB=ON \
    -DFT_REQUIRE_PNG=ON \
    -DFT_REQUIRE_BZIP2=ON \
    -DFT_DISABLE_HARFBUZZ=ON \
    -DFT_DISABLE_BROTLI=ON
