#!/usr/bin/env bash
# recipes/clapack/build-wasi.sh — cross-compile CLAPACK to wasm32-wasi via wasi-sdk.
# CLAPACK 3.2.1's cmake has no install() rules, so we manually install
# libraries, headers, and a cmake config package (same pattern as build-wasm.sh).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

# Skip test executables — they can't link under wasi-libc.
sed -i '/add_subdirectory(TESTING)/d' "${CVC_SOURCE_DIR}/CMakeLists.txt"

_toolchain_args=()
if [[ -n "${_WASI_TOOLCHAIN}" ]]; then
    _toolchain_args+=(-DCMAKE_TOOLCHAIN_FILE="${_WASI_TOOLCHAIN}")
else
    _toolchain_args+=(
        -DCMAKE_SYSTEM_NAME=WASI
        -DCMAKE_SYSTEM_PROCESSOR=wasm32
        -DCMAKE_C_COMPILER="${CC}"
        -DCMAKE_CXX_COMPILER="${CXX}"
        -DCMAKE_AR="${AR}"
        -DCMAKE_RANLIB="${RANLIB}"
        -DCMAKE_SYSROOT="${_WASI_SYSROOT}"
        -DCMAKE_C_COMPILER_TARGET=wasm32-wasip1
        -DCMAKE_CXX_COMPILER_TARGET=wasm32-wasip1
    )
fi

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_C_FLAGS="-Wno-implicit-function-declaration" \
    -DBUILD_TESTING=OFF \
    "${_toolchain_args[@]}"

cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"

mkdir -p "${CVC_INSTALL_DIR}/lib" "${CVC_INSTALL_DIR}/include" "${CVC_INSTALL_DIR}/lib/cmake/clapack"

cp "${CVC_BUILD_DIR}/F2CLIBS/libf2c/libf2c.a" "${CVC_INSTALL_DIR}/lib/"
cp "${CVC_BUILD_DIR}/BLAS/SRC/libblas.a"     "${CVC_INSTALL_DIR}/lib/"
cp "${CVC_BUILD_DIR}/SRC/liblapack.a"        "${CVC_INSTALL_DIR}/lib/"

cp "${CVC_SOURCE_DIR}/INCLUDE/blaswrap.h" "${CVC_INSTALL_DIR}/include/"
cp "${CVC_SOURCE_DIR}/INCLUDE/clapack.h"  "${CVC_INSTALL_DIR}/include/"
cp "${CVC_SOURCE_DIR}/INCLUDE/f2c.h"      "${CVC_INSTALL_DIR}/include/"

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
