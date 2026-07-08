#!/usr/bin/env bash
# recipes/python313t/build-wasm.sh — cross-compile free-threaded CPython 3.13
# for WASM (Emscripten).
#
# STATUS: Experimental.
#
# KNOWN BLOCKERS / NOTES:
#   - The free-threaded runtime relies on C11 atomics and OS-level mutex
#     primitives.  Emscripten maps these to WebAssembly Atomics
#     (wasm32 atomics proposal), which requires the resulting .wasm to be
#     served with Cross-Origin-Opener-Policy + Cross-Origin-Embedder-Policy
#     headers so the browser allocates a SharedArrayBuffer.
#   - We pass -matomics -mbulk-memory -pthread to emcc to opt in to
#     the atomics ABI.  Without these flags the GIL-disabled interpreter
#     will either fail to configure or produce a runtime that deadlocks
#     waiting for a lock that never wakes.
#   - Emscripten pthreads are cooperative and use Web Workers; each
#     "thread" is a separate Worker.  The free-threaded Python runtime
#     was not designed with this in mind, so behaviour under heavy
#     multi-thread workloads may be non-deterministic.
#   - Build output: python3.13t.js + python3.13t.wasm (no shared library).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

export PYTHON_VERSION="3.13.3"
export PYTHON_MINOR="3.13"
export PYTHON_LDVERSION="3.13t"
export PYTHON_DISABLE_GIL=1

# Opt in to Emscripten atomics ABI required by the free-threaded runtime.
export CFLAGS="${CFLAGS:-} -matomics -mbulk-memory -pthread"
export LDFLAGS="${LDFLAGS:-} -matomics -mbulk-memory -pthread"

source "${SCRIPT_DIR}/../_common/build-python.sh"
