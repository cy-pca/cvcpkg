#!/usr/bin/env bash
# recipes/mujoco/build-wasm.sh — cross-compile MuJoCo to wasm with Emscripten
# (platform wasm, and wasm-mt when CVC_WASM_THREADS=1).
#
# Upstream supports Emscripten directly: its top-level CMakeLists.txt builds
# libmujoco as a STATIC library under EMSCRIPTEN and adds wasm/, the official
# Embind JavaScript/TypeScript bindings (published on npm as @mujoco/mujoco).
# This bundle ships both halves:
#
#   * share/mujoco/wasm/mujoco.{js,wasm,d.ts} (+ mujoco.wasm.map): the JS
#     bindings, an ES module whose default export is `loadMujoco`.  Same build
#     flags as upstream's npm package (MODULARIZE, EXPORT_ES6, FORCE_FILESYSTEM,
#     memory growth, exceptions on).
#   * lib/libmujoco.a + include/mujoco/ + lib/cmake/mujoco/: for C++ programs
#     compiled to wasm (Ariadne's web hosts link C++, not the Embind layer).
#     Upstream installs nothing under EMSCRIPTEN, so this script collects the
#     archives itself.  The third-party archives libmujoco needs (libccd,
#     tinyxml2, qhullstatic_r, lodepng, tinyobjloader, miniz) go to
#     lib/mujoco/, NOT lib/, so they cannot shadow a cvcpkg tinyxml2 or qhull
#     package in a flat prefix.  The hand-written mujocoConfig.cmake links
#     libmujoco.a WHOLE_ARCHIVE (CMake >= 3.24): the OBJ/STL/gmsh mesh decoders
#     register themselves from static initialisers (mjPLUGIN_LIB_INIT) that a
#     plain static link drops, after which every mesh asset fails to load.
#     Upstream's JS link uses -Wl,--whole-archive for the same reason.
#
# Threads: upstream's MUJOCO_WASM_THREADS defaults to ON (-pthread everywhere,
# PTHREAD_POOL_SIZE=navigator.hardwareConcurrency).  It is pinned OFF for the
# wasm platform and ON for wasm-mt, because emscripten refuses to link
# -pthread objects into a non-shared-memory program and vice versa — exactly
# the split the wasm / wasm-mt platforms exist for.  A wasm-mt page must be
# served cross-origin isolated (COOP: same-origin, COEP: require-corp).
#
# Not built: MuJoCo Studio for the web (MUJOCO_BUILD_STUDIO, Filament + host
# tools), the wasm benchmark/tests (MUJOCO_BUILD_TESTS_WASM).  The engine
# plugins (actuator/sensor/sdf) are shared-library plugins upstream skips under
# EMSCRIPTEN.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/env-wasm.sh"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/deps.sh"

_threads=OFF
[[ "${CVC_WASM_THREADS:-0}" == "1" ]] && _threads=ON
echo "mujoco: wasm build, MUJOCO_WASM_THREADS=${_threads}"

mj_fetch_deps
mkdir -p "${CVC_BUILD_DIR}"
_log="${CVC_BUILD_DIR}/mujoco-build.log"
# Upstream writes the JS bindings to <source>/wasm/dist; start from an empty one.
rm -rf "${CVC_SOURCE_DIR}/wasm/dist"
cvc_cmake_build \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DMUJOCO_WASM_THREADS="${_threads}" \
    -DMUJOCO_BUILD_TESTS_WASM=OFF \
    -DMUJOCO_BUILD_STUDIO=OFF \
    -DMUJOCO_USE_FILAMENT=OFF \
    -DMUJOCO_CMAKE_DEP_CACHE="${MJ_DEP_CACHE}" \
    -DFETCHCONTENT_SOURCE_DIR_MARCHINGCUBECPP="${MJ_MARCHINGCUBECPP_DIR}" \
    2>&1 | tee "${_log}"
mj_check_no_git_fetch "${_log}"

# ── JS bindings ────────────────────────────────────────────────────────────
_dist="${CVC_SOURCE_DIR}/wasm/dist"
_js="${CVC_INSTALL_DIR}/share/mujoco/wasm"
mkdir -p "${_js}"
for _f in mujoco.js mujoco.wasm mujoco.d.ts; do
    [[ -f "${_dist}/${_f}" ]] || { echo "mujoco: ${_dist}/${_f} was not built" >&2; exit 1; }
    cp -f "${_dist}/${_f}" "${_js}/"
done
[[ -f "${_dist}/mujoco.wasm.map" ]] && cp -f "${_dist}/mujoco.wasm.map" "${_js}/"
cp -f "${CVC_SOURCE_DIR}/wasm/README.md" "${_js}/README.md"

# ── static libraries, headers, CMake package ───────────────────────────────
_find_one() {   # _find_one <archive-name>: exactly one match under the build tree
    local hits=() h
    while IFS= read -r -d '' h; do hits+=("${h}"); done \
        < <(find "${CVC_BUILD_DIR}" -type f -name "$1" -print0)
    [[ ${#hits[@]} -eq 1 ]] || { echo "mujoco: expected one $1, found ${#hits[@]}" >&2; return 1; }
    printf '%s\n' "${hits[0]}"
}
mkdir -p "${CVC_INSTALL_DIR}/lib/mujoco" "${CVC_INSTALL_DIR}/include/mujoco"
cp -f "$(_find_one libmujoco.a)" "${CVC_INSTALL_DIR}/lib/libmujoco.a"
_deplibs="ccd tinyxml2 qhullstatic_r lodepng tinyobjloader miniz"
for _d in ${_deplibs}; do
    cp -f "$(_find_one "lib${_d}.a")" "${CVC_INSTALL_DIR}/lib/mujoco/lib${_d}.a"
done
cp -f "${CVC_SOURCE_DIR}"/include/mujoco/*.h "${CVC_INSTALL_DIR}/include/mujoco/"

# The version upstream's project() declares, e.g. "VERSION 4.0.0".
_ver="$(sed -n 's/^[[:space:]]*VERSION[[:space:]]\{1,\}\([0-9][0-9.]*\)[[:space:]]*$/\1/p' \
        "${CVC_SOURCE_DIR}/CMakeLists.txt" | head -n 1)"
[[ -n "${_ver}" ]] || { echo "mujoco: could not read the version from CMakeLists.txt" >&2; exit 1; }
_cmk="${CVC_INSTALL_DIR}/lib/cmake/mujoco"
mkdir -p "${_cmk}"
for _t in mujocoConfig mujocoConfigVersion; do
    sed -e "s/@MUJOCO_VERSION@/${_ver}/g" \
        -e "s/@MUJOCO_WASM_THREADS@/${_threads}/g" \
        -e "s/@MUJOCO_DEP_LIBS@/${_deplibs}/g" \
        "${SCRIPT_DIR}/wasm/${_t}.cmake.in" > "${_cmk}/${_t}.cmake"
done

mj_install_licenses "${CVC_INSTALL_DIR}/share/licenses/mujoco"
cvc_rewrite_install_paths
