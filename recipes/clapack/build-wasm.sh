#!/usr/bin/env bash
# recipes/clapack/build-wasm.sh — cross-compile CLAPACK to wasm.
# CLAPACK is a C translation of reference LAPACK (f2c'd).
# Provides BLAS + LAPACK for wasm targets.
# Note: CLAPACK 3.2.1's cmake has no install() rules, so we manually
# install libraries, headers, and a cmake config package.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

# CLAPACK 3.2.1 unconditionally does add_subdirectory(TESTING) — the test
# executables can't link under emscripten. Patch CMakeLists.txt to skip it.
sed -i '/add_subdirectory(TESTING)/d' "${CVC_SOURCE_DIR}/CMakeLists.txt"

# CLAPACK 3.2.1 is old C (f2c output from 2008); emcc/clang treats
# implicit function declarations as errors. Suppress them.
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_TOOLCHAIN_FILE="${EMSDK}/upstream/emscripten/cmake/Modules/Platform/Emscripten.cmake" \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_C_FLAGS="-Wno-implicit-function-declaration" \
    -DBUILD_TESTING=OFF
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"

# Manual install — CLAPACK's cmake has no install() commands.
mkdir -p "${CVC_INSTALL_DIR}/lib"
mkdir -p "${CVC_INSTALL_DIR}/include"
mkdir -p "${CVC_INSTALL_DIR}/lib/cmake/clapack"

# Libraries
cp "${CVC_BUILD_DIR}/F2CLIBS/libf2c/libf2c.a" "${CVC_INSTALL_DIR}/lib/"
cp "${CVC_BUILD_DIR}/BLAS/SRC/libblas.a" "${CVC_INSTALL_DIR}/lib/"
cp "${CVC_BUILD_DIR}/SRC/liblapack.a" "${CVC_INSTALL_DIR}/lib/"

# Headers
cp "${CVC_SOURCE_DIR}/INCLUDE/blaswrap.h" "${CVC_INSTALL_DIR}/include/"
cp "${CVC_SOURCE_DIR}/INCLUDE/clapack.h" "${CVC_INSTALL_DIR}/include/"
cp "${CVC_SOURCE_DIR}/INCLUDE/f2c.h" "${CVC_INSTALL_DIR}/include/"

# Generate cmake config package so find_package(clapack CONFIG) works.
cat > "${CVC_INSTALL_DIR}/lib/cmake/clapack/clapack-config.cmake" << 'EOF'
get_filename_component(_clapack_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

if(NOT TARGET f2c)
  add_library(f2c STATIC IMPORTED)
  set_target_properties(f2c PROPERTIES
    IMPORTED_LOCATION "${_clapack_prefix}/lib/libf2c.a"
    INTERFACE_INCLUDE_DIRECTORIES "${_clapack_prefix}/include"
  )
endif()

if(NOT TARGET blas)
  add_library(blas STATIC IMPORTED)
  set_target_properties(blas PROPERTIES
    IMPORTED_LOCATION "${_clapack_prefix}/lib/libblas.a"
    INTERFACE_LINK_LIBRARIES "f2c"
  )
endif()

if(NOT TARGET lapack)
  add_library(lapack STATIC IMPORTED)
  set_target_properties(lapack PROPERTIES
    IMPORTED_LOCATION "${_clapack_prefix}/lib/liblapack.a"
    INTERFACE_INCLUDE_DIRECTORIES "${_clapack_prefix}/include"
    INTERFACE_LINK_LIBRARIES "blas;f2c"
  )
endif()

set(clapack_FOUND TRUE)
EOF

cat > "${CVC_INSTALL_DIR}/lib/cmake/clapack/clapack-config-version.cmake" << 'EOF'
set(PACKAGE_VERSION "3.2.1")
if(NOT ${PACKAGE_FIND_VERSION} VERSION_GREATER ${PACKAGE_VERSION})
  set(PACKAGE_VERSION_COMPATIBLE 1)
  if(${PACKAGE_FIND_VERSION} VERSION_EQUAL ${PACKAGE_VERSION})
    set(PACKAGE_VERSION_EXACT 1)
  endif()
endif()
EOF

