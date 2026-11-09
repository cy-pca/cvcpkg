#!/usr/bin/env bash
# recipes/mpfr/build-wasm.sh — cross-compile MPFR to wasm and wasm-mt via Emscripten.
#
# wasm-mt is the same script: env-wasm-mt.sh (sourced through ${CVC_PLATFORM})
# adds -pthread to every compile and link, and it links against gmp's wasm-mt
# bundle. MPFR keeps its default precision, exponent range, flags and constant
# caches (pi, log 2, ...) in globals unless it is built thread-safe, so wasm-mt
# requires --enable-thread-safe: those become thread-local, and configure fails
# rather than quietly building without TLS.
#
# Stack: MPFR, like GMP (see gmp's build-wasm.sh), takes temporaries from the
# stack. On emscripten's default 64 KB stack, transcendental functions at
# 30,000 bits of precision overflow it; 10,000 bits fit. A consumer working at
# that size links with a larger -sSTACK_SIZE (1 MB carried 300,000 bits).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

# With CFLAGS unset, MPFR's configure compiles with autoconf's -g -O2 (it does
# not copy gmp.h's flags, since emconfigure sets CC). On wasm-mt
# env-wasm-mt.sh has set CFLAGS to -pthread, which alone would build MPFR
# unoptimised, so name the flags here for both rows.
export CFLAGS="${CFLAGS:-} -g -O2"

thread_safe=()
if [[ "${CVC_WASM_THREADS:-0}" == "1" ]]; then
    thread_safe=(--enable-thread-safe)
fi

emconfigure ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=none-none-none \
    --disable-shared \
    --enable-static \
    --with-gmp="${CVC_DEPS_PREFIX}" \
    ${thread_safe[@]+"${thread_safe[@]}"}

emmake make -j "${CVC_JOBS}"
emmake make install

# Ensure installed .pc files are relocatable.
cvc_rewrite_install_paths

[ -e "${CVC_INSTALL_DIR}/lib/libmpfr.a" ] || { echo "mpfr: libmpfr.a not installed" >&2; exit 1; }
if [[ "${CVC_WASM_THREADS:-0}" == "1" ]]; then
    grep -q -- '-DMPFR_USE_THREAD_SAFE=1' "${CVC_SOURCE_DIR}/Makefile" \
        || { echo "mpfr: wasm-mt build is not thread-safe (no MPFR_USE_THREAD_SAFE)" >&2; exit 1; }
    echo "mpfr: thread-safe (TLS) build"
fi
