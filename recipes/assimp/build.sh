#!/usr/bin/env bash
# recipes/assimp/build.sh — build Open Asset Import Library (assimp) with CMake.
#
# assimp honors BUILD_SHARED_LIBS (set from CVC_LINK by cvc_cmake_build) for
# both static and shared link modes.  We use the cvcpkg zlib rather than the
# bundled copy via -DASSIMP_BUILD_ZLIB=OFF, which makes assimp's CMake run
# find_package(ZLIB) against CMAKE_PREFIX_PATH.  All other contrib deps
# (rapidjson, utf8cpp, minizip, poly2tri, ...) remain vendored.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}${CMAKE_PREFIX_PATH:+:${CMAKE_PREFIX_PATH}}"

cvc_cmake_build \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DASSIMP_BUILD_TESTS=OFF \
    -DASSIMP_BUILD_ASSIMP_TOOLS=OFF \
    -DASSIMP_INSTALL_PDB=OFF \
    -DASSIMP_WARNINGS_AS_ERRORS=OFF \
    -DASSIMP_BUILD_ZLIB=OFF \
    -DASSIMP_BUILD_GLTF_IMPORTER=ON \
    -DASSIMP_BUILD_OBJ_IMPORTER=ON \
    -DASSIMP_BUILD_FBX_IMPORTER=ON \
    -DASSIMP_BUILD_PLY_IMPORTER=ON \
    -DASSIMP_BUILD_STL_IMPORTER=ON
