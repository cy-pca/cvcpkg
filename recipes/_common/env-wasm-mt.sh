#!/usr/bin/env bash
# recipes/_common/env-wasm-mt.sh — the threaded-wasm build environment.
#
# wasm-mt is env-wasm.sh with -pthread injected into every compile and link
# (emscripten atomics + shared memory).  It is a separate PLATFORM, not a
# flag, because wasm-ld refuses to mix the two worlds ("--shared-memory is
# disallowed" when a single-threaded archive meets a -pthread link) — so the
# catalog must never hand a wasm bundle to a wasm-mt consumer or vice versa.
#
# Recipes that source env-${CVC_PLATFORM}.sh get threading automatically;
# recipes whose build-wasm.sh sources env-wasm.sh directly get it from the
# CVC_WASM_THREADS env their wasm-mt matrix entry sets.  Both funnel through
# the same hook in env-wasm.sh.
export CVC_WASM_THREADS=1
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env-wasm.sh"
