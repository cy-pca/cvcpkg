#!/usr/bin/env bash
# recipes/joltphysics/test.sh — smoke-test the installed Jolt package.
#
# Builds recipes/joltphysics/smoke (a find_package(Jolt) consumer) against
# CVC_INSTALL_DIR and RUNS it: natively as a host binary, on wasm under node via
# the emsdk that env-wasm.sh activated.  The consumer calls JPH::RegisterTypes(),
# which std::abort()s on any JPH_* configuration mismatch between library and
# consumer, so this is the ABI check the recipe description promises.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../_common" && pwd)"
SMOKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/smoke" && pwd)"
# shellcheck disable=SC1091
source "${_COMMON_DIR}/cvc_wasm_run.sh"

echo "-- joltphysics smoke test (${CVC_PLATFORM:-native}) --"

test -f "${CVC_INSTALL_DIR}/include/Jolt/Jolt.h" \
    || { echo "FAIL: include/Jolt/Jolt.h not found"; exit 1; }
test -f "${CVC_INSTALL_DIR}/lib/cmake/Jolt/JoltConfig.cmake" \
    || { echo "FAIL: lib/cmake/Jolt/JoltConfig.cmake not found"; exit 1; }
echo "  OK: headers + CMake package present"

TMPDIR_T=$(mktemp -d)
trap 'rm -rf "${TMPDIR_T}"' EXIT

if [[ "${CVC_PLATFORM:-}" == "wasm" ]]; then
    compgen -G "${CVC_INSTALL_DIR}/lib/libJolt.a" >/dev/null \
        || { echo "FAIL: lib/libJolt.a not found"; exit 1; }
    echo "  OK: libJolt.a found"
    if [[ "${CVC_WASM_RUNNER}" == "skip" ]]; then
        echo "  WARN: emsdk/node unavailable, skipping compile+run"
        exit 0
    fi
    # Same toolchain file env-wasm.sh's cvc_cmake_build used.  Emscripten's
    # toolchain sets CMAKE_FIND_ROOT_PATH_MODE_PACKAGE=ONLY, so the install
    # prefix must be given as a find root, not just a prefix path.
    cmake -G Ninja -S "${SMOKE_DIR}" -B "${TMPDIR_T}/b" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_TOOLCHAIN_FILE="${EMSDK}/upstream/emscripten/cmake/Modules/Platform/Emscripten.cmake" \
        -DCMAKE_FIND_ROOT_PATH="${CVC_INSTALL_DIR}" \
        -DCMAKE_PREFIX_PATH="${CVC_INSTALL_DIR}"
    cmake --build "${TMPDIR_T}/b"
    cvc_wasm_run "${TMPDIR_T}/b/jolt_smoke.js"
    echo "  OK: emcmake consumer built + ran under node"
elif [[ "${CVC_PLATFORM:-}" == "wasi" || "${CVC_PLATFORM:-}" == "cosmo" ]]; then
    # wasi is not a declared platform; cosmo APEs are not runnable in the
    # builder sandbox by default.  Presence check only.
    compgen -G "${CVC_INSTALL_DIR}/lib/libJolt.a" >/dev/null \
        || { echo "FAIL: lib/libJolt.a not found"; exit 1; }
    echo "  OK: libJolt.a found (no run on ${CVC_PLATFORM})"
else
    if [[ "${CVC_PLATFORM:-}" == "windows" && -n "${CVC_WINHOST:-}" ]]; then
        echo "  WARN: host-delegated Windows build; consumer compile+run skipped"
        exit 0
    fi
    if [[ "${CVC_PLATFORM:-}" == "windows" ]] \
        && ! command -v cl >/dev/null 2>&1 && ! command -v cc >/dev/null 2>&1; then
        echo "  WARN: no C++ compiler on PATH in the test shell; consumer compile+run skipped"
        exit 0
    fi
    # The env-<platform>.sh scripts need build-only variables (CVC_SOURCE_DIR,
    # CVC_BUILD_DIR), so mirror the bits a consumer build needs instead:
    # pkgsrc/ports tool paths AFTER our own prefix, the cvcpkg-built ninja
    # when present (NetBSD's pkgsrc ninja crashes when invoked from a clean
    # environment), and the build's own compiler preference on the BSDs.
    case "${CVC_PLATFORM:-}" in
        netbsd)          export PATH="${PATH}:/usr/pkg/bin:/usr/pkg/sbin" ;;
        freebsd|openbsd) export PATH="${PATH}:/usr/local/bin" ;;
    esac
    case "${CVC_PLATFORM:-}" in
        netbsd|freebsd|openbsd)
            if command -v clang++ >/dev/null 2>&1; then
                export CC="${CC:-clang}" CXX="${CXX:-clang++}"
            fi ;;
    esac
    _ninja=""
    if [[ -n "${CVC_DEPS_PREFIX:-}" && -x "${CVC_DEPS_PREFIX}/bin/ninja" ]]; then
        _ninja="${CVC_DEPS_PREFIX}/bin/ninja"
    fi
    echo "  tools: cmake=$(command -v cmake || echo MISSING) ninja=${_ninja:-$(command -v ninja || echo MISSING)} CXX=${CXX:-default}"
    cmake -G Ninja -S "${SMOKE_DIR}" -B "${TMPDIR_T}/b" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH="${CVC_INSTALL_DIR}" \
        ${_ninja:+-DCMAKE_MAKE_PROGRAM="${_ninja}"}
    cmake --build "${TMPDIR_T}/b"
    exe="${TMPDIR_T}/b/jolt_smoke"
    [[ -x "${exe}.exe" ]] && exe="${exe}.exe"
    # Shared bundles: the consumer needs the prefix's lib dir (bin on Windows)
    # at run time.
    PATH="${CVC_INSTALL_DIR}/bin:${PATH}" \
    LD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib:${LD_LIBRARY_PATH:-}" \
    DYLD_LIBRARY_PATH="${CVC_INSTALL_DIR}/lib:${DYLD_LIBRARY_PATH:-}" \
        "${exe}"
    echo "  OK: native consumer built + ran"
fi

echo "-- joltphysics smoke test passed --"
