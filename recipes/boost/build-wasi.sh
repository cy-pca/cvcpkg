#!/usr/bin/env bash
# recipes/boost/build-wasi.sh — cross-compile Boost to wasm32-wasi via wasi-sdk.
#
# This closes the closure hole for `cgal`, which claims wasi and
# runtime-depends on boost (CGAL's Boost usage is header-only:
# graph, property_map, variant, multiprecision, iterator).
#
# The wasi exclusion list is a strict superset of the wasm one.  wasm32-wasip1
# is single-threaded with no signals, no process control and no dynamic
# loading, so on top of the wasm exclusions we also drop:
#
#   thread     — wasi-libc exports pthread_* symbols, but pthread_create
#                fails at runtime on wasip1; Boost.Thread's config probes
#                also want pthread_condattr_setclock / PTHREAD_STACK_MIN.
#   contract   — pulls in Boost.Thread for its thread-safe assertion mode.
#   test       — the execution monitor needs sigaction(); wasi-libc has no
#                <signal.h> handlers at all.
#   locale     — depends on std::locale facets and ICU/iconv backends that
#                wasi-libc + libc++ only stub out.
#   wave       — depends on Boost.Thread and Boost.Filesystem.
#   filesystem — wasi-libc has the syscalls, but every path is relative to a
#                preopened directory, so the absolute-path semantics
#                Boost.Filesystem assumes do not hold.  Excluded rather than
#                shipped subtly broken; no cvcpkg consumer of boost/wasi
#                needs it.
#
# Unlike the wasm build we do NOT define BOOST_HAS_PTHREADS: Emscripten
# supplies working no-op pthread stubs in single-threaded mode, wasip1 does
# not, so Boost is left to select its single-threaded code paths.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

cvc_cmake_build \
    -DBOOST_ENABLE_CMAKE=ON \
    -DBUILD_TESTING=OFF \
    -DBOOST_INSTALL_LAYOUT=system \
    -DBOOST_EXCLUDE_LIBRARIES="context;coroutine;fiber;stacktrace;asio;cobalt;log;process;beast;thread;contract;test;locale;wave;filesystem"
