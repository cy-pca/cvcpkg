#!/usr/bin/env bash
# recipes/libsodium/test.sh -- check the installed libsodium by building a
# consumer against it and running it.
#
# The consumer checks every primitive TX.WTF's end-to-end encryption uses,
# with known answers where a standard gives one:
#   - X25519: RFC 7748 6.1 (both public keys and the shared secret);
#   - Ed25519: RFC 8032 7.1 test 1 (public key and signature), then verify;
#   - BLAKE2b-512("abc"): RFC 7693 appendix A; keyed generichash is consistent;
#   - XChaCha20-Poly1305 IETF, crypto_secretbox and crypto_box_seal: round trips,
#     and a flipped byte or a wrong AD refused;
#   - Argon2id (crypto_pwhash at the minimum limits): deterministic per salt,
#     and a str/str_verify round trip.
#
# Every platform also checks that the consumer surface is there: sodium.h,
# lib/pkgconfig/libsodium.pc and lib/cmake/sodium.
#   linux, macos: cc links lib/libsodium.a (the bundle's own code, whatever the
#     loader would find) and runs the consumer.
#   wasm, wasm-mt: compiled with the build's emsdk and run under node.  On
#     wasm-mt the checks also run on four pthreads, with randomness from
#     getentropy() through randombytes_set_implementation (see build-wasm.sh).
#   windows: run under bash without vcvars (CVC_WINHOST), so the files only.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
INC="${CVC_INSTALL_DIR}/include"
LIB="${CVC_INSTALL_DIR}/lib"

echo "-- libsodium test (${CVC_PLATFORM:-native}) --"
for f in "${INC}/sodium.h" "${INC}/sodium/version.h" "${LIB}/pkgconfig/libsodium.pc" \
         "${LIB}/cmake/sodium/sodiumConfig.cmake" "${LIB}/cmake/sodium/sodiumConfigVersion.cmake"; do
    test -f "${f}" || { echo "FAIL: ${f#"${CVC_INSTALL_DIR}"/} not installed"; exit 1; }
done
if [[ -d "${INC}/sodium/private" ]]; then
    echo "FAIL: include/sodium/private is installed (libsodium's internal headers)"
    exit 1
fi
grep -q '"1.0.22"' "${INC}/sodium/version.h" || { echo "FAIL: version.h is not 1.0.22"; exit 1; }
echo "  OK: headers, pkg-config and CMake package present"

CHECK_C="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/check.c"
test -f "${CHECK_C}" || { echo "FAIL: check.c not beside test.sh"; exit 1; }
TMPDIR_T=$(mktemp -d)
trap 'rm -rf "${TMPDIR_T}"' EXIT

case "${CVC_PLATFORM:-}" in
    wasm|wasm-mt)
        test -f "${LIB}/libsodium.a" || { echo "FAIL: lib/libsodium.a not found"; exit 1; }
        case "$(uname -s)" in
            MINGW*|MSYS*|CYGWIN*)
                echo "  OK: lib/libsodium.a present (no node run on a Windows host)"
                exit 0
                ;;
        esac
        # cvc_wasm_run.sh activates emsdk only for CVC_PLATFORM=wasm; wasm-mt runs
        # on the same emcc and node (as recipes/gmp's test.sh does).
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
        em_flags=(-O2 -sEXIT_RUNTIME=1 -sALLOW_MEMORY_GROWTH=1)
        if [[ "${CVC_PLATFORM}" == "wasm-mt" ]]; then
            # Pre-spawned workers: main blocks in pthread_join, so a worker started
            # on demand would never get the event loop it needs to start.
            em_flags+=(-pthread -DCVC_THREADS=4 -sPTHREAD_POOL_SIZE=4)
        fi
        cvc_wasm_cc "${TMPDIR_T}/t.js" "${CHECK_C}" "${em_flags[@]}" -lsodium
        cvc_wasm_run "${TMPDIR_T}/t.js" || { echo "FAIL: libsodium checks under node"; exit 1; }
        echo "libsodium test: OK (${CVC_PLATFORM}, run under node)"
        exit 0
        ;;
    windows)
        test -f "${LIB}/libsodium.lib" || { echo "FAIL: lib/libsodium.lib not found"; exit 1; }
        echo "  OK: lib/libsodium.lib present (build.ps1 built and ran check.c with MSVC)"
        exit 0
        ;;
esac

if [[ -n "${CVC_WINHOST:-}" ]]; then
    echo "  OK: files present (no compiler in a Windows host's test shell)"
    exit 0
fi
CC_BIN="${CC:-cc}"
command -v "${CC_BIN}" >/dev/null 2>&1 || { echo "FAIL: no C compiler (${CC_BIN})"; exit 1; }
test -f "${LIB}/libsodium.a" || { echo "FAIL: lib/libsodium.a not found"; exit 1; }
extra=()
[[ "${CVC_PLATFORM:-}" == "linux" ]] && extra+=(-lpthread)
"${CC_BIN}" -O2 -I"${INC}" "${CHECK_C}" "${LIB}/libsodium.a" ${extra[@]+"${extra[@]}"} -o "${TMPDIR_T}/t"
"${TMPDIR_T}/t" || { echo "FAIL: libsodium checks"; exit 1; }
echo "libsodium test: OK"
