#!/usr/bin/env bash
# recipes/libigl/test.sh — smoke-test the installed libigl package.
#
# Runs in builder.run_test's environment, NOT the build env: only CVC_PREFIX,
# CVC_INSTALL_DIR (this recipe's own staged tree), CVC_DEPS_PREFIX (where the
# eigen bundle is), CVC_BUILD_PREFIX, CVC_PLATFORM (+ CVC_WINHOST, cross-toolchain
# vars) are set, so env-<platform>.sh cannot be sourced.  Pattern: joltphysics.
#
# On a Windows host this shell has neither the MSVC environment nor an activated
# emsdk, so build.ps1 (windows, and wasm via build-wasm.ps1) builds and runs
# smoke/ itself while those are active; here only the layout is checked.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../_common" && pwd)"
SMOKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/smoke" && pwd)"

echo "-- libigl smoke test (${CVC_PLATFORM:-native}) --"
for f in include/igl/cotmatrix.h include/igl/AABB.cpp include/igl/raytri.c \
         include/igl/Singular_Value_Decomposition_Preamble.hpp \
         lib/cmake/libigl/libigl-config.cmake lib/cmake/libigl/libigl-config-version.cmake \
         lib/cmake/libigl/LibiglConfigTargets.cmake; do
    test -f "${CVC_INSTALL_DIR}/${f}" || { echo "FAIL: ${f} not found"; exit 1; }
done
grep -qF 'add_library(igl::core ' "${CVC_INSTALL_DIR}/lib/cmake/libigl/LibiglConfigTargets.cmake" \
    || { echo "FAIL: LibiglConfigTargets.cmake does not export igl::core"; exit 1; }
echo "  OK: headers + CMake package present"

_windows_host=0
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) _windows_host=1 ;; esac

# Both roots: libigl lives in CVC_INSTALL_DIR, its Eigen dep in CVC_DEPS_PREFIX.
_roots="${CVC_INSTALL_DIR}${CVC_DEPS_PREFIX:+;${CVC_DEPS_PREFIX}}"
TMPDIR_T=$(mktemp -d)
trap 'rm -rf "${TMPDIR_T}"' EXIT

case "${CVC_PLATFORM:-}" in
  wasm)
    if [[ "${_windows_host}" == 1 ]]; then
        echo "  OK: Windows host -- build-wasm.ps1 built and ran smoke/ under emsdk"
    else
        # shellcheck disable=SC1091
        source "${_COMMON_DIR}/cvc_wasm_run.sh"
        if [[ "${CVC_WASM_RUNNER}" == "skip" ]]; then
            echo "  WARN: emsdk/node unavailable, skipping compile+run"; exit 0
        fi
        # The Emscripten toolchain sets CMAKE_FIND_ROOT_PATH_MODE_PACKAGE=ONLY, so
        # both roots must be find roots, not just prefix paths.
        cmake -G Ninja -S "${SMOKE_DIR}" -B "${TMPDIR_T}/b" -DCMAKE_BUILD_TYPE=Release \
            -DCMAKE_TOOLCHAIN_FILE="${EMSDK}/upstream/emscripten/cmake/Modules/Platform/Emscripten.cmake" \
            -DCMAKE_FIND_ROOT_PATH="${_roots}" -DCMAKE_PREFIX_PATH="${_roots}" \
            -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF
        cmake --build "${TMPDIR_T}/b"
        cvc_wasm_run "${TMPDIR_T}/b/igl_smoke.js"
        echo "  OK: emcmake consumer built + ran under node"
    fi ;;
  wasm-mt)
    echo "  OK: presence check only on ${CVC_PLATFORM}" ;;
  windows)
    if [[ -n "${CVC_WINHOST:-}" ]]; then
        echo "  WARN: host-delegated Windows build; consumer compile+run skipped"
    else
        echo "  OK: build.ps1 built and ran smoke/ with MSVC"
    fi ;;
  *)
    # Mirror what a consumer build needs from env-<platform>.sh: pkgsrc/ports
    # tool paths AFTER our own prefix, the cvcpkg-built ninja when present
    # (NetBSD's pkgsrc ninja crashes from a clean environment), and the build's
    # own compiler preference on the BSDs.
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
    for _root in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
        if [[ -n "${_root}" && -x "${_root}/bin/ninja" ]]; then _ninja="${_root}/bin/ninja"; break; fi
    done
    cmake -G Ninja -S "${SMOKE_DIR}" -B "${TMPDIR_T}/b" -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH="${_roots}" -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF \
        ${_ninja:+-DCMAKE_MAKE_PROGRAM="${_ninja}"}
    cmake --build "${TMPDIR_T}/b"
    "${TMPDIR_T}/b/igl_smoke"
    echo "  OK: native consumer built + ran" ;;
esac
echo "-- libigl smoke test passed --"
