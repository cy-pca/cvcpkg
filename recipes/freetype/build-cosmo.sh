#!/usr/bin/env bash
# recipes/freetype/build-cosmo.sh — cross-compile FreeType with Cosmopolitan.
#
# Same story as the other cross targets: the CMake build's platform layer is
# ANSI-C stdio, and zlib, libpng and bzip2 all cover cosmo already.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX:-}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cvc_cmake_build \
    -DFT_REQUIRE_ZLIB=ON \
    -DFT_REQUIRE_PNG=ON \
    -DFT_REQUIRE_BZIP2=ON \
    -DFT_DISABLE_HARFBUZZ=ON \
    -DFT_DISABLE_BROTLI=ON
