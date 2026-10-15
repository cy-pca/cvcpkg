#!/usr/bin/env bash
# recipes/libuv/build.sh — build libuv from source (Linux, macOS, BSDs).
#
# libuv's CMake always builds the static uv_a (installed as libuv.a) and, with
# LIBUV_BUILD_SHARED, the shared uv (libuv.so.1 / libuv.1.dylib). It ignores
# BUILD_SHARED_LIBS for that choice, so CVC_LINK is mapped onto its own switch.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

_shared=ON
[ "${CVC_LINK:-shared}" = "static" ] && _shared=OFF

# ENABLE_CLANG_TIDY defaults ON upstream and hooks clang-tidy into every compile
# when the builder happens to have it installed; it is a lint, not part of the
# library, and must not make the build depend on what the host carries.
cvc_cmake_build \
    -DLIBUV_BUILD_SHARED="${_shared}" \
    -DLIBUV_BUILD_TESTS=OFF \
    -DLIBUV_BUILD_BENCH=OFF \
    -DENABLE_CLANG_TIDY=OFF \
    -DCMAKE_INSTALL_LIBDIR=lib
