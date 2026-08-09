#!/usr/bin/env bash
# recipes/brotli/build-wasm.sh — cross-compile Brotli to wasm via Emscripten.
#
# libbrotli{common,dec,enc} are pure computation: no syscalls, no threads,
# no dependencies — the most portable shape a C library comes in.  Brotli's
# own CMakeLists even probes for Emscripten (BROTLI_EMSCRIPTEN) and forces
# BUILD_SHARED_LIBS off for it, so this needs no extra coaxing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cvc_cmake_build \
    -DBROTLI_DISABLE_TESTS=ON
