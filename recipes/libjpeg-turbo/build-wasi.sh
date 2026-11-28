#!/usr/bin/env bash
# recipes/libjpeg-turbo/build-wasi.sh — cross-compile libjpeg-turbo to wasm32-wasi.
# SIMD/ASM is disabled (no NASM for wasm32).
#
# The libraries' error handling is setjmp/longjmp, which env-wasi.sh's wasm
# exception-handling flags provide.  The project also links tjunittest and
# tjbench, which it builds unconditionally (3.1 has no switch for them) and
# which call getpid(); wasip1 has no processes, so they link wasi-libc's
# emulation.  Only those executables reference it: the archives do not.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

export CFLAGS="${CFLAGS} -D_WASI_EMULATED_GETPID"
export LDFLAGS="${LDFLAGS} -lwasi-emulated-getpid"

cvc_cmake_build \
    -DENABLE_SHARED=OFF \
    -DENABLE_STATIC=ON \
    -DWITH_TURBOJPEG=ON \
    -DWITH_JAVA=OFF \
    -DWITH_SIMD=OFF \
    -DREQUIRE_SIMD=OFF
