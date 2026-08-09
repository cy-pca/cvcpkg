#!/usr/bin/env bash
# recipes/abseil/build-cosmo.sh — cross-compile Abseil with Cosmopolitan.
#
# Abseil's portability layer keys off the usual __linux__/__ELF__ feature
# macros, which cosmocc defines, and env-cosmo.sh already tells CMake
# CMAKE_SYSTEM_NAME=Linux.  Everything Abseil needs from the platform
# (pthreads, clock_gettime, futex-or-fallback, mmap) is provided by the
# Cosmopolitan libc.
#
# This closes the closure hole for `re2`, which claims cosmo and
# runtime-depends on abseil.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cvc_cmake_build \
    -DABSL_BUILD_TESTING=OFF \
    -DABSL_USE_GOOGLETEST_HEAD=OFF \
    -DCMAKE_CXX_STANDARD=17
