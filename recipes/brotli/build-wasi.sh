#!/usr/bin/env bash
# recipes/brotli/build-wasi.sh — cross-compile Brotli to wasm32-wasi via wasi-sdk.
#
# The three libraries are pure computation with no platform dependencies.
# The `brotli` CLI that CMakeLists.txt also builds is the only part that
# touches the OS, and everything it uses — utime, chmod, isatty, unlink,
# open/read/write — is exported by wasi-libc, so it compiles too (subject to
# the usual wasip1 preopened-directory rules at run time).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_cmake_build \
    -DBROTLI_DISABLE_TESTS=ON
