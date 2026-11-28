#!/usr/bin/env bash
# recipes/wamr/test.sh -- check the installed WAMR by building a consumer of
# lib/libiwasm.a and running it (check.c, through the wasm-c-api: a module
# instantiated with a host import that re-enters it, its memory read, written
# and grown, and a host trap).
#
#   linux, macos, the BSDs, haiku: cc links lib/libiwasm.a and runs the check.
#   windows: run under bash without vcvars (CVC_WINHOST), so the files only.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
INC="${CVC_INSTALL_DIR}/include"
LIB="${CVC_INSTALL_DIR}/lib"

echo "-- wamr test (${CVC_PLATFORM:-native}) --"
for f in "${INC}/wasm_c_api.h" "${INC}/wasm_export.h" "${INC}/lib_export.h" \
         "${LIB}/cmake/iwasm/iwasmConfig.cmake" "${LIB}/cmake/iwasm/iwasmConfigVersion.cmake"; do
    test -f "${f}" || { echo "FAIL: ${f#"${CVC_INSTALL_DIR}"/} not installed"; exit 1; }
done
echo "  OK: headers and CMake package present"

if [[ "${CVC_PLATFORM:-}" == "windows" || -n "${CVC_WINHOST:-}" ]]; then
    # libiwasm.lib, the runtime (static), not iwasm.lib, the CLI's import library.
    test -f "${LIB}/libiwasm.lib" || { echo "FAIL: lib/libiwasm.lib not installed"; exit 1; }
    echo "wamr test: OK (files only: no compiler in a Windows host's test shell)"
    exit 0
fi

CHECK_C="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check.c"
test -f "${CHECK_C}" || { echo "FAIL: check.c not beside test.sh"; exit 1; }
test -f "${LIB}/libiwasm.a" || { echo "FAIL: lib/libiwasm.a not found"; exit 1; }
TMPDIR_T=$(mktemp -d)
trap 'rm -rf "${TMPDIR_T}"' EXIT

CC_BIN="${CC:-cc}"
command -v "${CC_BIN}" >/dev/null 2>&1 || { echo "FAIL: no C compiler (${CC_BIN})"; exit 1; }
extra=()
# libiwasm.a calls pthread_* and libm; Haiku and macOS have both in libc.
case "${CVC_PLATFORM:-}" in
    linux) extra+=(-lpthread -lm -ldl) ;;
    freebsd|openbsd|netbsd) extra+=(-lpthread -lm) ;;
esac
"${CC_BIN}" -O2 -std=gnu99 -I"${INC}" "${CHECK_C}" "${LIB}/libiwasm.a" \
    ${extra[@]+"${extra[@]}"} -o "${TMPDIR_T}/t"
"${TMPDIR_T}/t" || { echo "FAIL: wamr checks"; exit 1; }
echo "wamr test: OK"
