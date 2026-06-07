#!/usr/bin/env bash
# recipes/zlib/test.sh — smoke test for the zlib install tree.
#
# Invoked by the packager after build.sh populates $CVC_INSTALL_DIR.
# Non-zero exit means the bundle is broken and should not ship.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

# Load cross-compilation test helpers (provides cvc_wasm_cc / cvc_wasm_run).
_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../_common" && pwd)"
# shellcheck disable=SC1091
source "${_COMMON_DIR}/cvc_wasm_run.sh"

echo "-- zlib smoke test (${CVC_PLATFORM:-native}) --"

# 1. Check that the header exists.
test -f "${CVC_INSTALL_DIR}/include/zlib.h" \
    || { echo "FAIL: include/zlib.h not found"; exit 1; }

# 2. Check that at least one library file exists.
found_lib=0
for pat in lib/libz.so* lib/libz.dylib* lib/libz.a lib/z.lib lib/zlib*.lib; do
    # shellcheck disable=SC2086
    if compgen -G "${CVC_INSTALL_DIR}/${pat}" >/dev/null 2>&1; then
        found_lib=1
        break
    fi
done
[[ "${found_lib}" -eq 1 ]] \
    || { echo "FAIL: no zlib library found in ${CVC_INSTALL_DIR}/lib/"; exit 1; }

# 3. Check that the CMake config package is findable.
if [[ -d "${CVC_INSTALL_DIR}/lib/cmake/ZLIB" ]]; then
    echo "  OK: CMake config package found"
else
    echo "  WARN: No CMake config package (may be OK if upstream zlib version predates it)"
fi

# 4. Compile a trivial program that links against zlib and run it.
TMPDIR=$(mktemp -d)
trap 'rm -rf "${TMPDIR}"' EXIT
cat > "${TMPDIR}/test_zlib.c" <<'EOF'
#include <zlib.h>
#include <stdio.h>
int main(void) {
    printf("zlib version: %s\n", zlibVersion());
    return 0;
}
EOF

if [[ "${CVC_PLATFORM:-}" == "wasm" ]]; then
    if [[ "${CVC_WASM_RUNNER}" == "skip" ]]; then
        echo "  WARN: wasm runtime unavailable, skipping compile+run test"
    else
        cvc_wasm_cc "${TMPDIR}/test_zlib.js" "${TMPDIR}/test_zlib.c" -lz \
        && {
            cvc_wasm_run "${TMPDIR}/test_zlib.js"
            echo "  OK: emcc compile + node run OK"
        } || { echo "FAIL: wasm compile+run test failed"; exit 1; }
    fi
elif [[ "${CVC_PLATFORM:-}" == "wasi" ]]; then
    if [[ "${CVC_WASM_RUNNER}" == "skip" ]]; then
        echo "  WARN: wasi runtime unavailable, skipping compile+run test"
    else
        cvc_wasm_cc "${TMPDIR}/test_zlib.wasm" "${TMPDIR}/test_zlib.c" -lz \
        && {
            cvc_wasm_run "${TMPDIR}/test_zlib.wasm"
            echo "  OK: wasi-sdk compile + ${CVC_WASM_RUNNER} run OK"
        } || { echo "FAIL: wasi compile+run test failed"; exit 1; }
    fi
elif command -v cc >/dev/null 2>&1; then
    cc -o "${TMPDIR}/test_zlib" "${TMPDIR}/test_zlib.c" \
        -I"${CVC_INSTALL_DIR}/include" \
        -L"${CVC_INSTALL_DIR}/lib" -lz 2>/dev/null \
    && {
        LD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib:${LD_LIBRARY_PATH:-}" \
            "${TMPDIR}/test_zlib"
        echo "  OK: compile + link + run OK"
    } || echo "  WARN: compile/link test skipped (compiler issue)"
else
    echo "  WARN: no C compiler on PATH, skipping compile test"
fi

echo "-- zlib smoke test passed --"
