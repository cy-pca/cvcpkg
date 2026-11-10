#!/usr/bin/env bash
# recipes/_common/env-wasm-mt.sh — the threaded-wasm build environment.
#
# wasm-mt is env-wasm.sh with -pthread injected into every compile and link
# (emscripten atomics + shared memory).  It is a separate PLATFORM, not a
# flag, so the catalog never hands a wasm bundle to a wasm-mt consumer or vice
# versa.  An object compiled without -pthread has its thread-locals and atomics
# stripped (they become plain globals and plain loads/stores), and wasm-ld then
# refuses it in a -pthread link ("--shared-memory is disallowed by X.o"): MPFR's
# thread-local state, C _Thread_local / C++ thread_local, <stdatomic.h> /
# std::atomic.  An object with neither links either way (a plain libgmp.a does,
# emsdk 5.0.7), so the link error does not catch every mix; the platform does.
#
# Recipes that source env-${CVC_PLATFORM}.sh get threading automatically;
# recipes whose build-wasm.sh sources env-wasm.sh directly get it from the
# CVC_WASM_THREADS env their wasm-mt matrix entry sets.  Both funnel through
# the same hook in env-wasm.sh.
export CVC_WASM_THREADS=1
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env-wasm.sh"
