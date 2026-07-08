#!/usr/bin/env bash
# recipes/python313t/build-wasi.sh — cross-compile free-threaded CPython 3.13
# for WASI.
#
# STATUS: Expected to fail — kept to track upstream progress.
#
# KNOWN BLOCKERS:
#   - Standard wasm32-wasip1 (wasi-sdk ≤ 22) has NO pthread support.
#     CPython's configure detects this via AC_CHECK_FUNC(pthread_create)
#     and will abort with:
#       "error: --disable-gil requires threading support"
#   - wasm32-wasip2 (Component Model preview-2) adds a threads proposal
#     but wasi-sdk support for it is still experimental as of mid-2026,
#     and CPython's Makefile.pre.in does not yet support wasip2.
#   - If/when the WASI threads proposal stabilises, update env-wasi.sh to
#     pass --target=wasm32-wasip2 and re-enable this matrix entry.
#   - Windows cross-compile is not relevant for WASI.
#
# Until the blocker is resolved this script will fail fast so CI can
# mark it as a known failure rather than a timeout.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

export PYTHON_VERSION="3.13.3"
export PYTHON_MINOR="3.13"
export PYTHON_LDVERSION="3.13t"
export PYTHON_DISABLE_GIL=1

# Attempt the build.  Expected to fail at configure time with
# "error: --disable-gil requires threading support" until WASI threads
# are stabilised.
source "${SCRIPT_DIR}/../_common/build-python.sh"
