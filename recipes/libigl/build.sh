#!/usr/bin/env bash
# recipes/libigl/build.sh — install libigl's permissive core (header-only).
#
# Upstream behaviours this script works around (build.ps1 mirrors every step):
#  1. cmake/recipes/external/eigen.cmake never calls find_package(): it returns
#     early iff an Eigen3::Eigen target already exists and otherwise
#     FetchContent-clones Eigen from gitlab.  So create the target from the
#     `eigen` recipe first, via CMAKE_PROJECT_libigl_INCLUDE (runs as the last
#     step of project(libigl), i.e. after the toolchain is loaded, in the
#     top-level scope the eigen.cmake guard sees).
#  2. Every LIBIGL_* module option defaults to ON for a top-level build, and each
#     module FetchContent-downloads its own deps.  All OFF except core;
#     FETCHCONTENT_FULLY_DISCONNECTED turns any stray download into a loud
#     configure failure instead of a silent network fetch.  The top-level
#     Matlab/MOSEK/BLAS probes run even with their modules OFF, so disable them.
#  3. igl_install.cmake sets EXPORT_NAME from ${module_export}, which upstream
#     never defines, so the installed target comes out as igl::igl_core.  The
#     cache value -Dmodule_export=core is what that function reads (it is only
#     called for igl_core), giving igl::core as in the build tree.
#  4. The core install globs only include/igl/*.h and *.cpp.  raytri.c, the five
#     Singular_Value_Decomposition_*.hpp kernels and IO are left behind, and
#     AABB, signed_distance, svd3x3 & co. include them, so copy the TOP-LEVEL
#     files of include/igl.  Never the subdirectories: copyleft/, triangle/,
#     matlab/, mosek/, embree/, opengl/ ... are other modules (some GPL or
#     non-commercial).
#  5. The package config lands in lib/cmake/igl/ next to LibiglConfigVersion.cmake
#     (from project(libigl VERSION 2.5.0)).  find_package(libigl) searches
#     <prefix>/lib/cmake/libigl*/ and pairs libigl-config.cmake only with
#     libigl-config-version.cmake, so move the dir and write that file.
#  6. libigl-config.cmake does find_dependency(Eigen3 REQUIRED) in module-first
#     mode, so any FindEigen3.cmake on the consumer's module path (CGAL's appends
#     one, which cannot parse Eigen 5's version) hijacks it.  Eigen 5 ships only a
#     config package: ask for CONFIG.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

mkdir -p "${CVC_BUILD_DIR}"
_pre="${CVC_BUILD_DIR}/cvcpkg-find-eigen3.cmake"
printf 'find_package(Eigen3 CONFIG REQUIRED)\n' > "${_pre}"

# Single-threaded wasm has no std::thread workers; everything else (wasm-mt
# included) keeps the default thread-pool backend.
_backend=POOL
[[ "${CVC_PLATFORM}" == "wasm" ]] && _backend=SERIAL

cvc_cmake_build \
    -DCMAKE_PROJECT_libigl_INCLUDE="${_pre}" \
    -DCMAKE_INSTALL_LIBDIR=lib \
    -DFETCHCONTENT_FULLY_DISCONNECTED=ON \
    -DCMAKE_FIND_USE_PACKAGE_REGISTRY=OFF \
    -DCMAKE_DISABLE_FIND_PACKAGE_Matlab=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_MOSEK=ON \
    -DCMAKE_DISABLE_FIND_PACKAGE_BLAS=ON \
    -DHUNTER_ENABLED=OFF \
    -Dmodule_export=core \
    -DLIBIGL_INSTALL=ON \
    -DLIBIGL_USE_STATIC_LIBRARY=OFF \
    -DLIBIGL_PARALLEL_FOR_BACKEND="${_backend}" \
    -DLIBIGL_BUILD_TESTS=OFF \
    -DLIBIGL_BUILD_TUTORIALS=OFF \
    -DLIBIGL_GLFW_TESTS=OFF \
    -DLIBIGL_WARNINGS_AS_ERRORS=OFF \
    -DLIBIGL_CYCODEBASE=OFF \
    -DLIBIGL_EMBREE=OFF \
    -DLIBIGL_GLFW=OFF \
    -DLIBIGL_IMGUI=OFF \
    -DLIBIGL_OPENGL=OFF \
    -DLIBIGL_STB=OFF \
    -DLIBIGL_PREDICATES=OFF \
    -DLIBIGL_SPECTRA=OFF \
    -DLIBIGL_XML=OFF \
    -DLIBIGL_COPYLEFT_CORE=OFF \
    -DLIBIGL_COPYLEFT_CGAL=OFF \
    -DLIBIGL_COPYLEFT_COMISO=OFF \
    -DLIBIGL_COPYLEFT_TETGEN=OFF \
    -DLIBIGL_RESTRICTED_MATLAB=OFF \
    -DLIBIGL_RESTRICTED_MOSEK=OFF \
    -DLIBIGL_RESTRICTED_TRIANGLE=OFF

_fail() { echo "libigl: $*" >&2; exit 1; }
_inc="${CVC_INSTALL_DIR}/include/igl"
_cm="${CVC_INSTALL_DIR}/lib/cmake"

# ── 4. complete the header-only tree (top level only) ──
find "${CVC_SOURCE_DIR}/include/igl" -maxdepth 1 -type f -exec cp -f {} "${_inc}/" \;
for f in raytri.c IO Singular_Value_Decomposition_Preamble.hpp AABB.h AABB.cpp; do
    [[ -f "${_inc}/${f}" ]] || _fail "include/igl/${f} missing after install"
done
[[ -z "$(find "${_inc}" -mindepth 1 -type d)" ]] \
    || _fail "include/igl has subdirectories (a non-core module leaked)"
[[ ! -e "${CVC_INSTALL_DIR}/include/Eigen" && ! -e "${_cm}/eigen" ]] \
    || _fail "a FetchContent Eigen was installed next to libigl"

# ── 5. make the package config findable as find_package(libigl [2.6]) ──
if [[ -d "${_cm}/igl" ]]; then
    rm -rf "${_cm}/libigl"
    mv "${_cm}/igl" "${_cm}/libigl"     # same depth: PACKAGE_PREFIX_DIR/_IMPORT_PREFIX stay valid
fi
_cfg="${_cm}/libigl/libigl-config.cmake"
_targets="${_cm}/libigl/LibiglConfigTargets.cmake"
[[ -f "${_cfg}" ]] || _fail "lib/cmake/libigl/libigl-config.cmake missing after install"
grep -qF 'add_library(igl::core ' "${_targets}" || _fail "exported target is not igl::core"
if [[ "${_backend}" == "SERIAL" ]]; then
    grep -qF 'IGL_PARALLEL_FOR_FORCE_SERIAL' "${_targets}" \
        || _fail "igl::core does not export IGL_PARALLEL_FOR_FORCE_SERIAL"
elif grep -qF 'IGL_PARALLEL_FOR_FORCE_SERIAL' "${_targets}"; then
    _fail "igl::core exports IGL_PARALLEL_FOR_FORCE_SERIAL on ${CVC_PLATFORM}"
fi

# Upstream's version file says 2.5.0 under a name CMake never pairs with
# libigl-config.cmake; replace it with CMake's own SameMajorVersion file.
# ARCH_INDEPENDENT: header-only, so no CMAKE_SIZEOF_VOID_P check.
rm -f "${_cm}/libigl/LibiglConfigVersion.cmake"
_ver="${CVC_BUILD_DIR}/cvcpkg-libigl-version.cmake"
printf '%s\n' \
    'cmake_minimum_required(VERSION 3.14)' \
    'include(CMakePackageConfigHelpers)' \
    'file(TO_CMAKE_PATH "${OUT}" OUT)' \
    'write_basic_package_version_file("${OUT}" VERSION "${VER}" COMPATIBILITY SameMajorVersion ARCH_INDEPENDENT)' \
    > "${_ver}"
cmake -DOUT="${_cm}/libigl/libigl-config-version.cmake" -DVER="${CVC_VERSION}" -P "${_ver}"

# ── 6. Eigen 5 is a config package ──
sed 's/find_dependency(Eigen3 REQUIRED)/find_dependency(Eigen3 CONFIG REQUIRED)/' \
    "${_cfg}" > "${_cfg}.tmp"
mv "${_cfg}.tmp" "${_cfg}"
grep -qF 'find_dependency(Eigen3 CONFIG REQUIRED)' "${_cfg}" \
    || _fail "could not pin find_dependency(Eigen3) to CONFIG mode"

install -d "${CVC_INSTALL_DIR}/share/licenses/libigl"
install -m 0644 "${CVC_SOURCE_DIR}/LICENSE.MPL2" "${CVC_INSTALL_DIR}/share/licenses/libigl/"
