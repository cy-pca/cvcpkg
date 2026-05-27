#!/usr/bin/env bash
# recipes/zlib/build-wasm.sh — cross-compile zlib to wasm via Emscripten.
#
# zlib 1.3.x unconditionally creates both SHARED and STATIC targets.
# On wasm, CMake converts SHARED→STATIC (TARGET_SUPPORTS_SHARED_LIBS=FALSE),
# and both get OUTPUT_NAME "z" on UNIX, so both output libz.a → Ninja error.
# Patch the source to skip the shared target before configuring.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

# Portable sed -i: macOS BSD sed requires '' as backup extension arg.
if [[ "$(uname)" == "Darwin" ]]; then
    _sed_i() { sed -i '' "$@"; }
else
    _sed_i() { sed -i "$@"; }
fi

# Patch: Remove the shared library target that conflicts on wasm.
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
