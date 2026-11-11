#!/usr/bin/env bash
# recipes/mpfr/test.sh — smoke-test the installed MPFR.
#
# Every platform: mpfr.h and a library are present.
#
# wasm and wasm-mt: a consumer built with the build's emsdk against this
# libmpfr.a and gmp's libgmp.a (from the dependency prefix) runs under node and
# checks correctly rounded results at set precisions: pi to 50 digits at 200
# bits, sqrt(2) to 30 digits at 100 bits, e at 53 bits. On wasm-mt it also
# checks the build is thread-safe: four pthreads each set their own default
# precision, wait for each other, then compute and find their own precision
# still in force. Without TLS the threads would share one default precision.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
INC="${CVC_INSTALL_DIR}/include"
LIB="${CVC_INSTALL_DIR}/lib"

echo "-- mpfr smoke test (${CVC_PLATFORM:-native}) --"

test -f "${INC}/mpfr.h" || { echo "FAIL: include/mpfr.h not found"; exit 1; }
compgen -G "${LIB}/libmpfr*" >/dev/null || compgen -G "${LIB}/mpfr*" >/dev/null \
    || { echo "FAIL: no libmpfr under lib/"; exit 1; }
echo "  OK: mpfr.h and the library are present"

if [[ "${CVC_PLATFORM:-}" != "wasm" && "${CVC_PLATFORM:-}" != "wasm-mt" ]]; then
    echo "mpfr test: OK"
    exit 0
fi

test -f "${LIB}/libmpfr.a" || { echo "FAIL: lib/libmpfr.a not found"; exit 1; }
: "${CVC_DEPS_PREFIX:?CVC_DEPS_PREFIX must be set (gmp is linked from it)}"
GMP_LIB="${CVC_DEPS_PREFIX}/lib/libgmp.a"
test -f "${GMP_LIB}" || { echo "FAIL: ${GMP_LIB} not found"; exit 1; }
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
        # build-wasm.ps1's row: the Windows emsdk has no sh launchers.
        echo "  OK: libmpfr.a present (no node run on a Windows host)"
        exit 0
        ;;
esac

# cvc_wasm_run.sh activates emsdk only for CVC_PLATFORM=wasm. wasm-mt runs on
# the same emcc and node, so source it as wasm and restore the platform (as
# joltphysics' test.sh does). Source it from inside the emsdk: where the fleet
# symlinks emsdk into the prefix, emsdk_env.sh finds itself only from there
# (see env-wasm.sh).
_cvc_common="$(cd "$(dirname "${BASH_SOURCE[0]}")/../_common" && pwd)"
_cvc_platform="${CVC_PLATFORM}" _cvc_pwd="${PWD}"
CVC_PLATFORM=wasm
cd "${CVC_EMSDK_DIR:-.}"
# shellcheck disable=SC1091
source "${_cvc_common}/cvc_wasm_run.sh"
cd "${_cvc_pwd}"
CVC_PLATFORM="${_cvc_platform}"
if [[ "${CVC_WASM_RUNNER}" == "skip" ]]; then
    echo "  WARN: emsdk/node unavailable, skipping compile+run"
    exit 0
fi

em_flags=(-O2 -sEXIT_RUNTIME=1)
if [[ "${CVC_PLATFORM}" == "wasm-mt" ]]; then
    # Pre-spawned workers: main blocks in pthread_join, so a worker started on
    # demand would never get the event loop it needs to start.
    em_flags+=(-pthread -DCVC_THREADS=4 -sPTHREAD_POOL_SIZE=4)
fi

TMPDIR_T=$(mktemp -d)
trap 'rm -rf "${TMPDIR_T}"' EXIT
cat > "${TMPDIR_T}/t.c" <<'EOF'
#include <stdio.h>
#include <string.h>
#include <gmp.h>
#include <mpfr.h>
#ifdef CVC_THREADS
#include <pthread.h>
#endif

/* Expected digits computed independently (Python's decimal module). The
   digit after each is 0 and 9: pi needs no rounding, sqrt(2) rounds up. */
#define PI50 "31415926535897932384626433832795028841971693993751"
#define SQRT2_30 "141421356237309504880168872421"

static int digits_are(mpfr_t x, size_t n, const char *want) {
  mpfr_exp_t e;
  char *s = mpfr_get_str(NULL, &e, 10, n, x, MPFR_RNDN);
  int ok = s != NULL && strcmp(s, want) == 0 && e == 1;
  mpfr_free_str(s);
  return ok;
}

/* Results at explicit precisions, then a variable initialised at the
   calling thread's default precision. */
static int check(mpfr_prec_t default_prec) {
  int ok = mpfr_get_default_prec() == default_prec;
  mpfr_t x, y;
  mpfr_init2(x, 200);
  mpfr_const_pi(x, MPFR_RNDN);
  ok &= mpfr_get_prec(x) == 200 && digits_are(x, 50, PI50);
  mpfr_set_prec(x, 100);
  mpfr_sqrt_ui(x, 2, MPFR_RNDN);
  ok &= digits_are(x, 30, SQRT2_30);
  /* e at 53 bits is the double nearest e: MPFR rounds correctly. */
  mpfr_set_prec(x, 53);
  mpfr_set_ui(x, 1, MPFR_RNDN);
  mpfr_exp(x, x, MPFR_RNDN);
  ok &= mpfr_get_d(x, MPFR_RNDN) == 2.718281828459045;
  mpfr_init(y);
  ok &= mpfr_get_prec(y) == default_prec;
  mpfr_clears(x, y, (mpfr_ptr)0);
  return ok;
}

#ifdef CVC_THREADS
static pthread_barrier_t all_set;

struct job {
  mpfr_prec_t prec;
  int ok;
};

static void *run(void *arg) {
  struct job *j = arg;
  /* A new thread starts at MPFR's default of 53 bits, whatever main set. */
  int ok = mpfr_get_default_prec() == 53;
  mpfr_set_default_prec(j->prec);
  /* Every thread has set its own precision before any of them checks. */
  pthread_barrier_wait(&all_set);
  ok &= check(j->prec);
  mpfr_free_cache();
  j->ok = ok;
  return NULL;
}
#endif

int main(void) {
  int ok = strcmp(mpfr_get_version(), "4.2.1") == 0;
  mpfr_set_default_prec(80);
  ok &= check(80);
  int threads = 0;
#ifdef CVC_THREADS
  ok &= mpfr_buildopt_tls_p() != 0;
  pthread_t th[CVC_THREADS];
  struct job jobs[CVC_THREADS];
  int started[CVC_THREADS];
  pthread_barrier_init(&all_set, NULL, CVC_THREADS);
  for (int i = 0; i < CVC_THREADS; ++i) {
    jobs[i].prec = 100 + 7 * i;
    jobs[i].ok = 0;
    started[i] = pthread_create(&th[i], NULL, run, &jobs[i]) == 0;
  }
  for (int i = 0; i < CVC_THREADS; ++i) {
    if (started[i])
      pthread_join(th[i], NULL);
    ok &= started[i] && jobs[i].ok;
    threads += started[i];
  }
  pthread_barrier_destroy(&all_set);
  /* Main's own default is untouched by the threads. */
  ok &= mpfr_get_default_prec() == 80;
#endif
  printf("mpfr %s (gmp %s, tls %d): checks %s (threads: %d)\n", mpfr_get_version(), gmp_version,
         mpfr_buildopt_tls_p(), ok ? "ok" : "FAILED", threads);
  mpfr_free_cache();
  return ok ? 0 : 1;
}
EOF
# -I/-L for this bundle come from cvc_wasm_cc; gmp comes from the dependency prefix.
cvc_wasm_cc "${TMPDIR_T}/t.js" "${TMPDIR_T}/t.c" -I"${CVC_DEPS_PREFIX}/include" \
    "${em_flags[@]}" "${LIB}/libmpfr.a" "${GMP_LIB}"
cvc_wasm_run "${TMPDIR_T}/t.js" || { echo "FAIL: mpfr under node"; exit 1; }
echo "mpfr test: OK (${CVC_PLATFORM}, run under node)"
