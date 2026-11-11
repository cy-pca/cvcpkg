#!/usr/bin/env bash
# recipes/gmp/test.sh — smoke-test the installed GMP, and on x86 prove the
# bundle is portable (not tuned to the builder's CPU).
#
# Every platform: gmp.h and a library are present.
#
# wasm and wasm-mt: mpz, mpq and gmpxx results are right in a consumer built
# with the build's emsdk and run under node; on wasm-mt also on four pthreads.
#
# x86/x86_64 builds from build.sh (linux, macos, the BSDs, haiku): the bundle
# must be a FAT binary. Through +cvc.10 configure took the BUILDER's CPU from
# config.guess, compiled that CPU's mpn assembly and recorded -march=<cpu> in
# gmp.h, and the result raised SIGILL on older x86_64 (BMI2/ADX on a Sandy
# Bridge Xeon). Fail if:
#   1. gmp.h's __GMP_CFLAGS carries a -march/-mtune (it would leak into every
#      consumer that copies it, MPFR first);
#   2. libgmp.a lacks __gmpn_cpuvec_init, the CPUID dispatcher a fat build
#      has and a CPU-specific build does not;
#   3. any function outside the CPU-specific kernels uses BMI2/ADX/LZCNT
#      (the +cvc.11 SIGILL: see check 3 below);
#   4. big-integer arithmetic through the dispatched kernels is wrong.
# A builder newer than the target cannot reproduce the SIGILL itself, so 1-3
# are the structural guarantee; 4 proves the dispatch runs.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
INC="${CVC_INSTALL_DIR}/include"
LIB="${CVC_INSTALL_DIR}/lib"

echo "-- gmp smoke test (${CVC_PLATFORM:-native}) --"

test -f "${INC}/gmp.h" || { echo "FAIL: include/gmp.h not found"; exit 1; }
compgen -G "${LIB}/libgmp*" >/dev/null || compgen -G "${LIB}/gmp*" >/dev/null \
    || { echo "FAIL: no libgmp under lib/"; exit 1; }
echo "  OK: gmp.h and the library are present"

# wasm / wasm-mt: compile a consumer with the emsdk the build used and RUN it
# under node. On wasm-mt the consumer is built with -pthread too and repeats the
# checks on four threads at once.
if [[ "${CVC_PLATFORM:-}" == "wasm" || "${CVC_PLATFORM:-}" == "wasm-mt" ]]; then
    test -f "${LIB}/libgmp.a" || { echo "FAIL: lib/libgmp.a not found"; exit 1; }
    test -f "${LIB}/libgmpxx.a" || { echo "FAIL: lib/libgmpxx.a not found"; exit 1; }
    case "$(uname -s)" in
        MINGW*|MSYS*|CYGWIN*)
            # build-wasm.ps1's row: the Windows emsdk has no sh launchers.
            echo "  OK: libgmp.a and libgmpxx.a present (no node run on a Windows host)"
            exit 0
            ;;
    esac
    # cvc_wasm_run.sh activates emsdk only for CVC_PLATFORM=wasm. wasm-mt runs on
    # the same emcc and node, so source it as wasm and restore the platform (as
    # joltphysics' test.sh does). Source it from inside the emsdk: where the
    # fleet symlinks emsdk into the prefix, emsdk_env.sh finds itself only from
    # there (see env-wasm.sh).
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
        # Pre-spawned workers: main blocks in pthread_join, so a worker started
        # on demand would never get the event loop it needs to start.
        em_flags+=(-pthread -DCVC_THREADS=4 -sPTHREAD_POOL_SIZE=4)
    fi
    TMPDIR_T=$(mktemp -d)
    trap 'rm -rf "${TMPDIR_T}"' EXIT
    cat > "${TMPDIR_T}/t.c" <<'EOF'
#include <gmp.h>
#include <stdio.h>
#include <string.h>
#ifdef CVC_THREADS
#include <pthread.h>
#endif

/* Expected values computed independently (Python's math.factorial and
   fractions.Fraction): the harmonic numbers H_30 and H_100 in lowest terms. */
#define H30 "9304682830147/2329089562800"
#define H100 "14466636279520351160221518043104131447711/2788815009188499086581352357412492142272"

static int check(void) {
  int ok = 1;
  mpz_t f, a, b, c, q, r;
  mpz_inits(f, a, b, c, q, r, NULL);
  /* 1000! has 2568 decimal digits and ends in exactly 249 zeros. */
  mpz_fac_ui(f, 1000);
  char *s = mpz_get_str(NULL, 10, f);
  size_t n = strlen(s), z = 0;
  while (z < n && s[n - 1 - z] == '0')
    ++z;
  ok &= n == 2568 && z == 249;
  void (*free_fn)(void *, size_t);
  mp_get_memory_functions(NULL, NULL, &free_fn);
  free_fn(s, n + 1);
  /* (3^5000 * 7^4000) / 7^4000 == 3^5000, remainder 0. */
  mpz_ui_pow_ui(a, 3, 5000);
  mpz_ui_pow_ui(b, 7, 4000);
  mpz_mul(c, a, b);
  mpz_tdiv_qr(q, r, c, b);
  ok &= mpz_cmp(q, a) == 0 && mpz_sgn(r) == 0;
  mpz_clears(f, a, b, c, q, r, NULL);
  /* Sum 1/k exactly; mpq_add keeps the result canonical. */
  mpq_t h, t, want;
  mpq_inits(h, t, want, NULL);
  for (unsigned long k = 1; k <= 100; ++k) {
    mpq_set_ui(t, 1, k);
    mpq_add(h, h, t);
    if (k == 30) {
      mpq_set_str(want, H30, 10);
      ok &= mpq_equal(h, want);
    }
  }
  mpq_set_str(want, H100, 10);
  ok &= mpq_equal(h, want);
  mpq_clears(h, t, want, NULL);
  return ok;
}

#ifdef CVC_THREADS
static void *run(void *res) {
  *(int *)res = check();
  return NULL;
}
#endif

int main(void) {
  int ok = strcmp(gmp_version, "6.3.0") == 0;
  ok &= check();
  int threads = 0;
#ifdef CVC_THREADS
  pthread_t th[CVC_THREADS];
  int res[CVC_THREADS], started[CVC_THREADS];
  for (int i = 0; i < CVC_THREADS; ++i) {
    res[i] = 0;
    started[i] = pthread_create(&th[i], NULL, run, &res[i]) == 0;
  }
  for (int i = 0; i < CVC_THREADS; ++i) {
    if (started[i])
      pthread_join(th[i], NULL);
    ok &= started[i] && res[i];
    threads += started[i];
  }
#endif
  printf("gmp %s: mpz/mpq checks %s (threads: %d)\n", gmp_version, ok ? "ok" : "FAILED", threads);
  return ok ? 0 : 1;
}
EOF
    cat > "${TMPDIR_T}/txx.cpp" <<'EOF'
#include <gmpxx.h>
#include <iostream>

int main() {
  mpq_class h = 0;
  for (int k = 1; k <= 30; ++k)
    h += mpq_class(1, k);
  mpz_class f;
  mpz_fac_ui(f.get_mpz_t(), 25);
  bool ok = h == mpq_class("9304682830147/2329089562800") && f == mpz_class("15511210043330985984000000");
  std::cout << "gmpxx: H_30 = " << h << ", 25! = " << f << (ok ? " ok" : " FAILED") << std::endl;
  return ok ? 0 : 1;
}
EOF
    cvc_wasm_cc "${TMPDIR_T}/t.js" "${TMPDIR_T}/t.c" "${em_flags[@]}" -lgmp
    cvc_wasm_run "${TMPDIR_T}/t.js" || { echo "FAIL: gmp arithmetic under node"; exit 1; }
    em++ -o "${TMPDIR_T}/txx.js" "${TMPDIR_T}/txx.cpp" -I"${INC}" -L"${LIB}" \
        "${em_flags[@]}" -lgmpxx -lgmp
    cvc_wasm_run "${TMPDIR_T}/txx.js" || { echo "FAIL: gmpxx under node"; exit 1; }
    echo "gmp test: OK (${CVC_PLATFORM}, run under node)"
    exit 0
fi

case "${CVC_PLATFORM:-}" in
    linux|macos|freebsd|openbsd|netbsd|haiku) ;;
    *)
        echo "  OK: ${CVC_PLATFORM:-this platform} builds without CPU-specific assembly; portability checks are for build.sh platforms"
        exit 0
        ;;
esac
case "$(uname -m)" in
    x86_64|amd64|i?86|i86pc|x86) ;;
    *)
        echo "  OK: $(uname -m) has no fat build; portability checks are x86-only"
        exit 0
        ;;
esac

# 1. No CPU-specific flags recorded for consumers.
gmp_cflags=$(grep -E '^#define __GMP_CFLAGS ' "${INC}/gmp.h" || true)
echo "  gmp.h: ${gmp_cflags}"
if grep -Eq -- '-m(arch|tune|cpu)=' <<<"${gmp_cflags}"; then
    echo "FAIL: __GMP_CFLAGS names a CPU; the bundle is tuned to the builder (configure without --enable-fat?)"
    exit 1
fi
echo "  OK: __GMP_CFLAGS is CPU-neutral"

# 2. The fat CPUID dispatcher is linked in.
if [[ ! -f "${LIB}/libgmp.a" ]]; then
    echo "FAIL: lib/libgmp.a not found (build.sh always builds the static archive)"
    exit 1
fi
if ! command -v nm >/dev/null 2>&1; then
    if [[ "${CVC_PLATFORM}" == "haiku" ]]; then
        echo "  WARN: no nm on this host; skipping the dispatcher check"
    else
        echo "FAIL: nm not found"
        exit 1
    fi
else
    # Capture first, then grep: `nm | grep -q` under pipefail fails spuriously,
    # because grep -q exits at the first match and nm then dies of SIGPIPE on a
    # large archive (seen on macOS).
    nm_out=$(nm "${LIB}/libgmp.a" 2>/dev/null || true)
    if [[ -z "${nm_out}" ]]; then
        echo "FAIL: nm could not read lib/libgmp.a"
        exit 1
    fi
    if ! grep -q 'gmpn_cpuvec_init' <<<"${nm_out}"; then
        echo "FAIL: libgmp.a has no __gmpn_cpuvec_init: not a fat build, so its mpn kernels are the builder's CPU's"
        exit 1
    fi
    echo "  OK: fat build (__gmpn_cpuvec_init present)"
fi

# 3. No BMI2/ADX/LZCNT outside the CPU-specific kernels. A fat build may hold
# mulx/adcx/adox in kernels named for a CPU that has them (__gmpn_addmul_1_zen,
# ..._coreihwl); the dispatcher picks those only on such a CPU. Anything else (the
# C code, the _x86_64 baseline, the _fat dispatchers) runs on every CPU and must
# not. +cvc.11 passed the other checks and still raised SIGILL on a Sandy Bridge Xeon:
# configure took host_cpu from GMP's config.guess, so longlong.h inlined mulx
# into 76 plain-C functions (__gmpz_n_pow_ui first). lzcnt is listed because an
# older CPU decodes it as bsr and silently computes the wrong answer; tzcnt is
# not, since compilers emit it as `rep bsf`, which every x86_64 runs.
if ! command -v objdump >/dev/null 2>&1; then
    if [[ "${CVC_PLATFORM}" == "haiku" ]]; then
        echo "  WARN: no objdump on this host; skipping the instruction scan"
    else
        echo "FAIL: objdump not found"
        exit 1
    fi
else
    kernels='k8|k10|bd1|bd2|bd4|bt1|bt2|zen|zen2|zen3|zen4|core2|coreinhm|coreisbr|coreihwl|coreibwl|skylake|atom|silvermont|goldmont|nano|pentium4'
    # GNU and LLVM objdump both print "<symbol>:" headers and the mnemonic after a
    # tab; LLVM adds an AT&T size suffix (mulxq). Capture first, as above.
    dis=$(objdump -d --no-show-raw-insn "${LIB}/libgmp.a" 2>/dev/null || true)
    if [[ -z "${dis}" ]]; then
        echo "FAIL: objdump could not disassemble lib/libgmp.a"
        exit 1
    fi
    bad=$(awk -v kernels="${kernels}" '
        /^[0-9a-fA-F]+ <[^>]+>:$/ { fn = $2; gsub(/[<>:]/, "", fn); next }
        /[[:space:]](mulx|adcx|adox|shlx|shrx|sarx|rorx|andn|bzhi|pdep|pext|lzcnt)[bwlq]?[[:space:]]/ {
            if (fn !~ ("_(" kernels ")$")) seen[fn] = 1
        }
        END { for (f in seen) print f }' <<<"${dis}" | sort)
    if [[ -n "${bad}" ]]; then
        echo "FAIL: BMI2/ADX/LZCNT instructions outside the CPU-specific kernels, in:"
        head -20 <<<"${bad}" | sed 's/^/    /'
        echo "  ($(wc -l <<<"${bad}" | tr -d ' ') functions; configure must use a generic --build triplet)"
        exit 1
    fi
    echo "  OK: no BMI2/ADX/LZCNT outside the CPU-specific kernels"
fi

# 4. Arithmetic through the dispatched kernels.
CC_BIN="${CC:-cc}"
if ! command -v "${CC_BIN}" >/dev/null 2>&1; then
    if [[ "${CVC_PLATFORM}" == "haiku" ]]; then
        echo "  WARN: no C compiler on this host; skipping the run check"
        exit 0
    fi
    echo "FAIL: no C compiler (${CC_BIN})"
    exit 1
fi
TMPDIR_T=$(mktemp -d)
trap 'rm -rf "${TMPDIR_T}"' EXIT
cat > "${TMPDIR_T}/t.c" <<'EOF'
#include <gmp.h>
#include <stdio.h>
#include <string.h>

int main(void) {
  /* 1000! has 2568 decimal digits and ends in exactly 249 zeros. */
  mpz_t f, a, b, c, q, r;
  mpz_inits(f, a, b, c, q, r, NULL);
  mpz_fac_ui(f, 1000);
  char *s = mpz_get_str(NULL, 10, f);
  size_t n = strlen(s), z = 0;
  while (z < n && s[n - 1 - z] == '0')
    ++z;
  /* Large multiply and exact division go through mul/sqr/div kernels the fat
     dispatcher selects: (3^5000 * 7^4000) / 7^4000 == 3^5000, remainder 0. */
  mpz_ui_pow_ui(a, 3, 5000);
  mpz_ui_pow_ui(b, 7, 4000);
  mpz_mul(c, a, b);
  mpz_tdiv_qr(q, r, c, b);
  int ok = n == 2568 && z == 249 && mpz_cmp(q, a) == 0 && mpz_sgn(r) == 0;
  printf("digits=%zu zeros=%zu quotient_ok=%d remainder_zero=%d\n", n, z, mpz_cmp(q, a) == 0,
         mpz_sgn(r) == 0);
  mpz_clears(f, a, b, c, q, r, NULL);
  return ok ? 0 : 1;
}
EOF
# Link the static archive so the test exercises exactly this bundle, whatever
# libgmp the host's loader would otherwise find.
"${CC_BIN}" -O2 -I"${INC}" "${TMPDIR_T}/t.c" "${LIB}/libgmp.a" -o "${TMPDIR_T}/t"
"${TMPDIR_T}/t" || { echo "FAIL: arithmetic check"; exit 1; }
echo "  OK: arithmetic through the dispatched kernels"
echo "gmp test: OK"
