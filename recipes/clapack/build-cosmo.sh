#!/usr/bin/env bash
# recipes/clapack/build-cosmo.sh — cross-compile CLAPACK with Cosmopolitan.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

# CLAPACK 3.2.1 unconditionally does add_subdirectory(TESTING).
sed -i '/add_subdirectory(TESTING)/d' "${CVC_SOURCE_DIR}/CMakeLists.txt"

# CLAPACK is old f2c output; suppress implicit function declaration errors.
cvc_cmake_build \
    -DCMAKE_C_FLAGS="-Wno-implicit-function-declaration" \
    -DBUILD_TESTING=OFF

# Manual install — CLAPACK's cmake has no install() commands.
mkdir -p "${CVC_INSTALL_DIR}/lib"
mkdir -p "${CVC_INSTALL_DIR}/include"
mkdir -p "${CVC_INSTALL_DIR}/lib/cmake/clapack"

cp "${CVC_BUILD_DIR}/F2CLIBS/libf2c/libf2c.a" "${CVC_INSTALL_DIR}/lib/"
cp "${CVC_BUILD_DIR}/BLAS/SRC/libblas.a" "${CVC_INSTALL_DIR}/lib/"
cp "${CVC_BUILD_DIR}/SRC/liblapack.a" "${CVC_INSTALL_DIR}/lib/"

cp "${CVC_SOURCE_DIR}/INCLUDE/blaswrap.h" "${CVC_INSTALL_DIR}/include/"
cp "${CVC_SOURCE_DIR}/INCLUDE/clapack.h" "${CVC_INSTALL_DIR}/include/"
cp "${CVC_SOURCE_DIR}/INCLUDE/f2c.h" "${CVC_INSTALL_DIR}/include/"

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
