#!/usr/bin/env bash
# recipes/freetype/build-wasm.sh — cross-compile FreeType to wasm via Emscripten.
#
# FreeType's platform layer is ANSI C: builds/unix is only used by its
# autotools path, and the CMake build compiles src/base/ftsystem.c, which
# talks to stdio and malloc and nothing else.  All three of FreeType's
# cvcpkg dependencies (zlib, libpng, bzip2) already cover wasm.
#
# HarfBuzz and Brotli stay disabled to match the native build: HarfBuzz would
# be a dependency cycle (it depends on FreeType), and Brotli is only used for
# WOFF2 decompression, which nothing in the catalog needs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX:-}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cvc_cmake_build \
    -DFT_REQUIRE_ZLIB=ON \
    -DFT_REQUIRE_PNG=ON \
    -DFT_REQUIRE_BZIP2=ON \
    -DFT_DISABLE_HARFBUZZ=ON \
    -DFT_DISABLE_BROTLI=ON
