#!/usr/bin/env bash
# recipes/boost/build-wasm.sh — cross-compile Boost to wasm.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

# Append to the env flags rather than clobbering them: an explicit
# -DCMAKE_CXX_FLAGS makes CMake ignore $CXXFLAGS entirely, which silently
# dropped env-wasm.sh's -pthread in CVC_WASM_THREADS=1 builds (the archives
# then lack the atomics/bulk-memory features and wasm-ld refuses to link them
# into a shared-memory program).
#
# BOOST_HAS_PTHREADS is only forced for the single-threaded build, where Boost
# must compile against Emscripten's pthread stubs it cannot detect. Under
# -pthread Boost's config detects pthreads itself, and redefining the macro is
# a -Werror=macro-redefined.
_boost_flags="${CXXFLAGS:-}"
if [[ "${CVC_WASM_THREADS:-0}" != "1" ]]; then
    _boost_flags="${_boost_flags} -DBOOST_HAS_PTHREADS"
fi
cvc_cmake_build \
    -DBOOST_ENABLE_CMAKE=ON \
    -DBUILD_TESTING=OFF \
    -DBOOST_INSTALL_LAYOUT=system \
    -DCMAKE_CXX_FLAGS="${_boost_flags}" \
    -DBOOST_EXCLUDE_LIBRARIES="context;coroutine;fiber;stacktrace;asio;cobalt;log;process;beast"
