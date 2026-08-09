#!/usr/bin/env bash
# recipes/expat/build-cosmo.sh — cross-compile Expat with Cosmopolitan.
#
# Expat has no dependencies, and every OS Cosmopolitan targets provides
# /dev/urandom (expat's default entropy source on UNIX-like systems), with
# the libc also exposing getrandom/arc4random.  env-cosmo.sh tells CMake
# CMAKE_SYSTEM_NAME=Linux, so expat's UNIX detection branch applies.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cvc_cmake_build \
    -DEXPAT_BUILD_TESTS=OFF \
    -DEXPAT_BUILD_EXAMPLES=OFF \
    -DEXPAT_BUILD_TOOLS=OFF \
    -DEXPAT_BUILD_DOCS=OFF \
    -DEXPAT_SHARED_LIBS=OFF
