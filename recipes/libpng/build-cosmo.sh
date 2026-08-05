#!/usr/bin/env bash
# recipes/libpng/build-cosmo.sh — cross-compile libpng with Cosmopolitan.
#
# Cosmopolitan gives libpng everything it wants (stdio, setjmp, malloc) and
# zlib already builds for cosmo.  Hardware optimizations are disabled because
# an APE is a single fat binary that must run on both x86-64 and aarch64
# hosts: baking in one architecture's intrinsics would defeat that.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX:-}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cvc_cmake_build \
    -DPNG_TESTS=OFF \
    -DPNG_TOOLS=OFF \
    -DPNG_SHARED=OFF \
    -DPNG_STATIC=ON \
    -DPNG_HARDWARE_OPTIMIZATIONS=OFF
