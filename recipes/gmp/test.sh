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
