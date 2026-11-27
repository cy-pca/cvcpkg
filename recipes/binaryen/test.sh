#!/usr/bin/env bash
# recipes/binaryen/test.sh -- check the installed binaryen: the tools run and
# say they're version 129, and a C++ consumer of lib/libbinaryen.a (check.cpp:
# WAT parsed, Asyncify around ari.*, the binary written and read back) builds
# and runs.
#
#   linux, macos, the BSDs, haiku: wasm-opt --version, then c++ links
#     lib/libbinaryen.a with check.cpp (-fno-rtti, as binaryen is built) and
#     runs it.
#   windows: run under bash without vcvars (CVC_WINHOST), so the files and
#     wasm-opt --version only.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
INC="${CVC_INSTALL_DIR}/include"
LIB="${CVC_INSTALL_DIR}/lib"
BIN="${CVC_INSTALL_DIR}/bin"

echo "-- binaryen test (${CVC_PLATFORM:-native}) --"
for f in "${INC}/binaryen-c.h" "${INC}/binaryen/wasm.h" "${INC}/binaryen/pass.h" \
         "${INC}/binaryen/parser/wat-parser.h" "${INC}/binaryen/fp16.h" \
         "${LIB}/cmake/binaryen/binaryenConfig.cmake"; do
    test -f "${f}" || { echo "FAIL: ${f#"${CVC_INSTALL_DIR}"/} not installed"; exit 1; }
done
echo "  OK: headers and CMake package present"

exe=""
if [[ "${CVC_PLATFORM:-}" == "windows" || -n "${CVC_WINHOST:-}" ]]; then
    exe=".exe"
fi
for tool in wasm-opt wasm-as wasm-dis; do
    test -x "${BIN}/${tool}${exe}" || { echo "FAIL: bin/${tool}${exe} not installed"; exit 1; }
done
version=$("${BIN}/wasm-opt${exe}" --version)
case "${version}" in
    *"version 129"*) echo "  OK: ${version}" ;;
    *) echo "FAIL: wasm-opt --version says '${version}'"; exit 1 ;;
esac

if [[ -n "${exe}" ]]; then
    test -f "${LIB}/binaryen.lib" || { echo "FAIL: lib/binaryen.lib not found"; exit 1; }
    echo "binaryen test: OK (no compiler in a Windows host's test shell)"
    exit 0
fi

CHECK_CPP="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check.cpp"
test -f "${CHECK_CPP}" || { echo "FAIL: check.cpp not beside test.sh"; exit 1; }
test -f "${LIB}/libbinaryen.a" || { echo "FAIL: lib/libbinaryen.a not found"; exit 1; }
TMPDIR_T=$(mktemp -d)
trap 'rm -rf "${TMPDIR_T}"' EXIT

CXX_BIN="${CXX:-c++}"
command -v "${CXX_BIN}" >/dev/null 2>&1 || { echo "FAIL: no C++ compiler (${CXX_BIN})"; exit 1; }
extra=()
case "${CVC_PLATFORM:-}" in
    linux|freebsd|openbsd|netbsd) extra+=(-lpthread) ;;
esac
"${CXX_BIN}" -std=c++17 -O1 -fno-rtti -I"${INC}" -I"${INC}/binaryen" "${CHECK_CPP}" \
    "${LIB}/libbinaryen.a" ${extra[@]+"${extra[@]}"} -o "${TMPDIR_T}/t"
"${TMPDIR_T}/t" || { echo "FAIL: binaryen checks"; exit 1; }
echo "binaryen test: OK"
