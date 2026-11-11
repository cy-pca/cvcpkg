#!/usr/bin/env bash
# recipes/mujoco/test.sh — smoke-test the installed MuJoCo package.
#
# Native: runs the installed `testspeed` sample on upstream's humanoid model
# (libmujoco + the installed models + RPATH/DLL lookup), checks the three
# plugins are in bin/mujoco_plugin, then builds and runs smoke/ — a
# find_package(mujoco) consumer that compiles an MJCF model with an OBJ mesh
# from an in-memory VFS and steps it.
# wasm / wasm-mt: builds and runs the same consumer with emscripten under node
# (it fails if the mesh decoders were dropped by the static link), then loads
# the installed JS bindings (share/mujoco/wasm/mujoco.js) with node and steps a
# model through them (smoke/bindings.mjs).
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"

_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../_common" && pwd)"
SMOKE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/smoke" && pwd)"
P="${CVC_INSTALL_DIR}"
_plat="${CVC_PLATFORM:-native}"

echo "-- mujoco smoke test (${_plat}) --"

fail() { echo "FAIL: $*"; exit 1; }

test -f "${P}/include/mujoco/mujoco.h" || fail "include/mujoco/mujoco.h not found"
test -f "${P}/lib/cmake/mujoco/mujocoConfig.cmake" || fail "lib/cmake/mujoco/mujocoConfig.cmake not found"
test -f "${P}/share/licenses/mujoco/LICENSE" || fail "share/licenses/mujoco/LICENSE not found"
echo "  OK: headers, CMake package and licence texts present"

TMPDIR_T=$(mktemp -d)
trap 'rm -rf "${TMPDIR_T}"' EXIT

case "${_plat}" in
wasm|wasm-mt)
    test -f "${P}/lib/libmujoco.a" || fail "lib/libmujoco.a not found"
    for _f in mujoco.js mujoco.wasm mujoco.d.ts; do
        test -f "${P}/share/mujoco/wasm/${_f}" || fail "share/mujoco/wasm/${_f} not found"
    done
    echo "  OK: libmujoco.a and JS bindings present"
    # cvc_wasm_run.sh only knows the "wasm" platform name; emsdk and node are
    # the same for wasm-mt.
    CVC_PLATFORM=wasm
    # shellcheck disable=SC1091
    source "${_COMMON_DIR}/cvc_wasm_run.sh"
    CVC_PLATFORM="${_plat}"
    if [[ "${CVC_WASM_RUNNER}" == "skip" ]]; then
        echo "  WARN: emsdk/node unavailable, skipping compile+run"
        exit 0
    fi
    _mt=()
    [[ "${_plat}" == "wasm-mt" ]] && _mt=(-DCMAKE_C_FLAGS=-pthread -DCMAKE_CXX_FLAGS=-pthread)
    # Emscripten's toolchain sets CMAKE_FIND_ROOT_PATH_MODE_PACKAGE=ONLY, so the
    # prefix must be a find root, not just a prefix path.
    cmake -G Ninja -S "${SMOKE_DIR}" -B "${TMPDIR_T}/b" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_TOOLCHAIN_FILE="${EMSDK}/upstream/emscripten/cmake/Modules/Platform/Emscripten.cmake" \
        -DCMAKE_FIND_ROOT_PATH="${P}" \
        -DCMAKE_PREFIX_PATH="${P}" \
        ${_mt[@]+"${_mt[@]}"}
    cmake --build "${TMPDIR_T}/b"
    cvc_wasm_run "${TMPDIR_T}/b/mujoco_smoke.js"
    echo "  OK: emscripten find_package(mujoco) consumer built + ran under node"
    cvc_wasm_run "${SMOKE_DIR}/bindings.mjs" "${P}/share/mujoco/wasm/mujoco.js"
    echo "  OK: JS bindings loaded + stepped under node"
    ;;
*)
    _n=$(find "${P}/bin/mujoco_plugin" -type f \( -name '*.so' -o -name '*.dylib' -o -name '*.dll' \) | wc -l)
    [[ ${_n} -eq 3 ]] || fail "expected 3 plugins in bin/mujoco_plugin, found ${_n}"
    echo "  OK: ${_n} plugins in bin/mujoco_plugin"

    _ts="${P}/bin/testspeed"
    [[ -x "${_ts}.exe" ]] && _ts="${_ts}.exe"
    [[ -x "${_ts}" ]] || fail "bin/testspeed not found"
    PATH="${P}/bin:${PATH}" \
    LD_LIBRARY_PATH="${P}/lib:${LD_LIBRARY_PATH:-}" \
        "${_ts}" --nstep=2000 "${P}/share/mujoco/model/humanoid/humanoid.xml" \
        | tee "${TMPDIR_T}/testspeed.txt"
    grep -q "Steps per second" "${TMPDIR_T}/testspeed.txt" || fail "testspeed printed no timing"
    echo "  OK: installed testspeed ran the humanoid model"

    if [[ "${_plat}" == "windows" && -n "${CVC_WINHOST:-}" ]]; then
        echo "  WARN: host-delegated Windows build; consumer compile+run skipped"
        exit 0
    fi
    if [[ "${_plat}" == "windows" ]] \
        && ! command -v cl >/dev/null 2>&1 && ! command -v c++ >/dev/null 2>&1; then
        echo "  WARN: no C++ compiler on PATH in the test shell; consumer compile+run skipped"
        exit 0
    fi
    _ninja=""
    if [[ -n "${CVC_DEPS_PREFIX:-}" && -x "${CVC_DEPS_PREFIX}/bin/ninja" ]]; then
        _ninja="${CVC_DEPS_PREFIX}/bin/ninja"
    fi
    cmake -G Ninja -S "${SMOKE_DIR}" -B "${TMPDIR_T}/b" \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_PREFIX_PATH="${P}" \
        ${_ninja:+-DCMAKE_MAKE_PROGRAM="${_ninja}"}
    cmake --build "${TMPDIR_T}/b"
    exe="${TMPDIR_T}/b/mujoco_smoke"
    [[ -x "${exe}.exe" ]] && exe="${exe}.exe"
    PATH="${P}/bin:${PATH}" \
    LD_LIBRARY_PATH="${P}/lib:${LD_LIBRARY_PATH:-}" \
    DYLD_LIBRARY_PATH="${P}/lib:${DYLD_LIBRARY_PATH:-}" \
        "${exe}"
    echo "  OK: native find_package(mujoco) consumer built + ran"
    ;;
esac

echo "-- mujoco smoke test passed --"
