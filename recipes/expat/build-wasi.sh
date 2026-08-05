#!/usr/bin/env bash
# recipes/expat/build-wasi.sh — cross-compile Expat to wasm32-wasi via wasi-sdk.
#
# Expat has no dependencies and needs nothing from the platform except an
# entropy source for its hash salt.  On wasip1 that resolves as follows:
#   * /dev/urandom does not exist (no device nodes), so EXPAT_DEV_URANDOM is
#     forced OFF rather than left to expat's UNIX default of ON;
#   * getrandom(2) is absent from wasi-libc, so the AUTO probe just fails —
#     which is fine, AUTO only SEND_ERRORs when explicitly set to ON;
#   * arc4random_buf IS exported by wasi-libc, and that is expat's preferred
#     source anyway.
# If none of those were available expat would still build, falling back to its
# time/PID-based gather_time_entropy(); the CMake build has no hard failure
# path here (unlike the autotools one).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_cmake_build \
    -DEXPAT_BUILD_TESTS=OFF \
    -DEXPAT_BUILD_EXAMPLES=OFF \
    -DEXPAT_BUILD_TOOLS=OFF \
    -DEXPAT_BUILD_DOCS=OFF \
    -DEXPAT_SHARED_LIBS=OFF \
    -DEXPAT_DEV_URANDOM=OFF
