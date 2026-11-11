#!/usr/bin/env bash
# recipes/gmp/build-wasm.sh — cross-compile GMP to wasm and wasm-mt via Emscripten.
# GMP uses autotools; we use emconfigure to wrap ./configure.
#
# Generic C only (--host=none-none-none, --disable-assembly): GMP has no wasm
# assembly, so every mpn kernel is the portable C one. Static only, like every
# emscripten build.
#
# wasm-mt is the same script: env-wasm-mt.sh (sourced through ${CVC_PLATFORM})
# adds -pthread to every compile and link, so the archives link into a
# shared-memory (pthreads) module. GMP is reentrant; its only process-wide
# state is the allocator a program may install once, at startup, with
# mp_set_memory_functions.
#
# Stack: GMP takes temporaries of up to 32 KB each from the stack (alloca),
# nested. On emscripten's default 64 KB stack (main thread and pthreads alike)
# operands around 100,000 bits overflow it, which without
# -sSTACK_OVERFLOW_CHECK silently corrupts memory; 30,000 bits fit. A consumer
# working at that size links with a larger -sSTACK_SIZE (1 MB carried 3,000,000
# bits; libcvc uses 2-4 MB, which pthreads inherit). Heap temporaries
# (--enable-alloca=malloc-reentrant) would avoid that, but cost a malloc, behind
# a lock on wasm-mt, for every temporary of every small operation.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

# GMP's configure chooses its own flags (-O2 -pedantic for this host) only when
# CFLAGS is unset. On wasm-mt env-wasm-mt.sh has set CFLAGS to -pthread, and
# GMP would then compile with that alone, unoptimised. So name GMP's choice
# here, for both rows; wasm-mt differs from wasm only by -pthread. CXXFLAGS
# likewise, for gmpxx.
export CFLAGS="${CFLAGS:-} -O2 -pedantic"
export CXXFLAGS="${CXXFLAGS:-} -O2"

# GMP cross-compilation needs CC_FOR_BUILD set to a native C compiler
# (for building host-side code generators like gen-fac). emconfigure sets
# CC=emcc but CC_FOR_BUILD must be the real host compiler.
# Also specify --build explicitly so configure properly detects cross-compilation.
export CC_FOR_BUILD="${CVC_HOST_CC:-cc}"
BUILD_TRIPLET=$(${CC_FOR_BUILD} -dumpmachine 2>/dev/null || echo "$(uname -m)-unknown-$(uname -s | tr '[:upper:]' '[:lower:]')")

# GMP's configure needs the host triplet for cross-compilation.
emconfigure ./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --host=none-none-none \
    --build="${BUILD_TRIPLET}" \
    --disable-shared \
    --enable-static \
    --enable-cxx \
    --disable-assembly

emmake make -j "${CVC_JOBS}"
emmake make install

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths

# gmp.h records the flags for consumers (MPFR's configure reads them): the
# optimisation named above on both rows, and -pthread on wasm-mt.
gmp_cflags=$(grep -E '^#define __GMP_CFLAGS ' "${CVC_INSTALL_DIR}/include/gmp.h" || true)
echo "gmp: ${gmp_cflags}"
grep -q -- '-O2' <<<"${gmp_cflags}" || { echo "gmp: __GMP_CFLAGS lacks -O2" >&2; exit 1; }
if [[ "${CVC_WASM_THREADS:-0}" == "1" ]]; then
    grep -q -- '-pthread' <<<"${gmp_cflags}" || { echo "gmp: wasm-mt build without -pthread" >&2; exit 1; }
fi
for lib in libgmp.a libgmpxx.a; do
    [ -e "${CVC_INSTALL_DIR}/lib/${lib}" ] || { echo "gmp: ${lib} not installed" >&2; exit 1; }
done
