#!/usr/bin/env bash
# recipes/zlib-ng/build.sh — build zlib-ng from source (Linux, macOS, BSDs).
#
# ZLIB_COMPAT=OFF: the native zng_* API (libz-ng, zlib-ng.h). A compat build
# would install a second libz.so/zlib.h and collide with cvcpkg's `zlib`.
# WITH_NATIVE_INSTRUCTIONS=OFF + WITH_RUNTIME_CPU_DETECTION=ON: portable binary
# that picks its SIMD kernels at runtime — never -march=native of the builder.
# BUILD_TESTING=OFF: the test suite fetches GoogleTest over the network.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DZLIB_COMPAT=OFF \
    -DBUILD_TESTING=OFF \
    -DWITH_GTEST=OFF \
    -DWITH_NATIVE_INSTRUCTIONS=OFF \
    -DWITH_RUNTIME_CPU_DETECTION=ON \
    -DCMAKE_INSTALL_LIBDIR=lib
