#!/usr/bin/env bash
# recipes/pcre2/test.sh — smoke-test PCRE2 installation.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

# Load cross-compilation test helpers (provides cvc_wasm_cc / cvc_wasm_run).
_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../_common" && pwd)"
# shellcheck disable=SC1091
source "${_COMMON_DIR}/cvc_wasm_run.sh"

echo "-- pcre2 smoke test (${CVC_PLATFORM:-native}) --"

# 1. Check that the header exists.
test -f "${CVC_INSTALL_DIR}/include/pcre2.h" \
    || { echo "FAIL: include/pcre2.h not found"; exit 1; }
echo "  OK: pcre2.h found"

if [[ "${CVC_PLATFORM:-}" == "wasm" || "${CVC_PLATFORM:-}" == "wasi" ]]; then
    # Cross-compiled: no native binaries, verify library + compile+run test.
    found_lib=0
    for pat in lib/libpcre2-8.a lib/libpcre2-8.so* lib/libpcre2-8.dylib*; do
        if compgen -G "${CVC_INSTALL_DIR}/${pat}" >/dev/null 2>&1; then
            found_lib=1; break
        fi
    done
    [[ "${found_lib}" -eq 1 ]] \
        || { echo "FAIL: no pcre2 library found"; exit 1; }
    echo "  OK: pcre2 library found"

    TMPDIR=$(mktemp -d)
    trap 'rm -rf "${TMPDIR}"' EXIT
    cat > "${TMPDIR}/test_pcre2.c" <<'TESTEOF'
#define PCRE2_CODE_UNIT_WIDTH 8
#include <pcre2.h>
#include <stdio.h>
int main(void) {
    int errcode; PCRE2_SIZE erroff;
    pcre2_code *re = pcre2_compile(
        (PCRE2_SPTR)"^hello", PCRE2_ZERO_TERMINATED,
        0, &errcode, &erroff, NULL);
    if (!re) { printf("FAIL: compile\n"); return 1; }
    pcre2_match_data *md = pcre2_match_data_create_from_pattern(re, NULL);
    int rc = pcre2_match(re, (PCRE2_SPTR)"hello world", 11, 0, 0, md, NULL);
    pcre2_match_data_free(md);
    pcre2_code_free(re);
    if (rc < 0) { printf("FAIL: match rc=%d\n", rc); return 1; }
    printf("pcre2 match OK (rc=%d)\n", rc);
    return 0;
}
TESTEOF

    if [[ "${CVC_WASM_RUNNER}" == "skip" ]]; then
        echo "  WARN: wasm runtime unavailable, skipping compile+run test"
    elif [[ "${CVC_PLATFORM:-}" == "wasm" ]]; then
        cvc_wasm_cc "${TMPDIR}/test_pcre2.js" "${TMPDIR}/test_pcre2.c" -lpcre2-8 \
        && {
            cvc_wasm_run "${TMPDIR}/test_pcre2.js"
            echo "  OK: emcc compile + node run OK"
        } || { echo "FAIL: wasm compile+run test failed"; exit 1; }
    else
        cvc_wasm_cc "${TMPDIR}/test_pcre2.wasm" "${TMPDIR}/test_pcre2.c" -lpcre2-8 \
        && {
            cvc_wasm_run "${TMPDIR}/test_pcre2.wasm"
            echo "  OK: wasi-sdk compile + ${CVC_WASM_RUNNER} run OK"
        } || { echo "FAIL: wasi compile+run test failed"; exit 1; }
    fi
else
    # Native platform: test binaries and pkg-config.
    echo 'Testing pcre2-config...'
    "${CVC_INSTALL_DIR}/bin/pcre2-config" --version

    echo 'Testing pcre2grep...'
    echo "hello world" | "${CVC_INSTALL_DIR}/bin/pcre2grep" "hello"

    echo 'Testing pkg-config...'
    PKG_CONFIG_PATH="${CVC_INSTALL_DIR}/lib/pkgconfig" pkg-config --modversion libpcre2-8
fi

echo '-- pcre2 smoke test passed --'
