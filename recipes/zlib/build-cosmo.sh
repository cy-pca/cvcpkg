#!/usr/bin/env bash
# recipes/zlib/build-cosmo.sh — cross-compile zlib to Cosmopolitan APE
# static archive via the cosmocc toolchain.
#
# zlib 1.3.x defines both SHARED and STATIC targets in the same
# CMakeLists.txt; on wasm we patch that away to avoid a name collision,
# and cosmo hits exactly the same issue (both target libz.a on UNIX).
# Reuse the same sed hack.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

# Portable in-place sed (unlikely to hit BSD sed since builder is linux,
# but keep the pattern for symmetry with build-wasm.sh).
if [[ "$(uname)" == "Darwin" ]]; then
    _sed_i() { sed -i '' "$@"; }
else
    _sed_i() { sed -i "$@"; }
fi

_sed_i '/^add_library(zlib SHARED/d' "${CVC_SOURCE_DIR}/CMakeLists.txt"
_sed_i 's/set_target_properties(zlib zlibstatic/set_target_properties(zlibstatic/' "${CVC_SOURCE_DIR}/CMakeLists.txt"
_sed_i 's/install(TARGETS zlib zlibstatic/install(TARGETS zlibstatic/' "${CVC_SOURCE_DIR}/CMakeLists.txt"
_sed_i '/target_include_directories(zlib /d' "${CVC_SOURCE_DIR}/CMakeLists.txt"
_sed_i '/set_target_properties(zlib /d' "${CVC_SOURCE_DIR}/CMakeLists.txt"
_sed_i 's/target_link_libraries(example zlib)/target_link_libraries(example zlibstatic)/' "${CVC_SOURCE_DIR}/CMakeLists.txt"
_sed_i 's/target_link_libraries(minigzip zlib)/target_link_libraries(minigzip zlibstatic)/' "${CVC_SOURCE_DIR}/CMakeLists.txt"
_sed_i 's/target_link_libraries(example64 zlib)/target_link_libraries(example64 zlibstatic)/' "${CVC_SOURCE_DIR}/CMakeLists.txt"
_sed_i 's/target_link_libraries(minigzip64 zlib)/target_link_libraries(minigzip64 zlibstatic)/' "${CVC_SOURCE_DIR}/CMakeLists.txt"

cvc_cmake_build \
    -DZLIB_BUILD_EXAMPLES=OFF \
    -DINSTALL_PKGCONFIG_DIR="${CVC_INSTALL_DIR}/lib/pkgconfig"
