#!/usr/bin/env bash
# recipes/gmp/test.sh — smoke-test the installed GMP, and on x86 prove the
# bundle is portable (not tuned to the builder's CPU).
#
# Every platform: gmp.h and a library are present.
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
#   3. big-integer arithmetic through the dispatched kernels is wrong.
# A builder newer than the target cannot reproduce the SIGILL itself, so 1 and
# 2 are the structural guarantee; 3 proves the dispatch runs.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
INC="${CVC_INSTALL_DIR}/include"
LIB="${CVC_INSTALL_DIR}/lib"

echo "-- gmp smoke test (${CVC_PLATFORM:-native}) --"

test -f "${INC}/gmp.h" || { echo "FAIL: include/gmp.h not found"; exit 1; }
compgen -G "${LIB}/libgmp*" >/dev/null || compgen -G "${LIB}/gmp*" >/dev/null \
    || { echo "FAIL: no libgmp under lib/"; exit 1; }
echo "  OK: gmp.h and the library are present"

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
elif ! nm "${LIB}/libgmp.a" 2>/dev/null | grep -q 'gmpn_cpuvec_init'; then
    echo "FAIL: libgmp.a has no __gmpn_cpuvec_init: not a fat build, so its mpn kernels are the builder's CPU's"
    exit 1
else
    echo "  OK: fat build (__gmpn_cpuvec_init present)"
fi

# 3. Arithmetic through the dispatched kernels.
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
