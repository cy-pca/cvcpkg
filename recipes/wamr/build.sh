#!/usr/bin/env bash
# recipes/wamr/build.sh — build WAMR from source on Linux/macOS.
#
# WAMR's CMakeLists.txt lives in product-mini/platforms/linux (or darwin).
# We use the top-level CMake entry point which delegates appropriately.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# WAMR's recommended build path is via the product-mini directory,
# but recent versions support a top-level CMake build too.
WAMR_CMAKE_DIR="${CVC_SOURCE_DIR}"
if [[ -f "${CVC_SOURCE_DIR}/product-mini/CMakeLists.txt" ]]; then
    WAMR_CMAKE_DIR="${CVC_SOURCE_DIR}/product-mini/platforms/${CVC_PLATFORM}"
fi

cmake -G Ninja \
    -S "${WAMR_CMAKE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DWAMR_BUILD_INTERP=1 \
    -DWAMR_BUILD_FAST_INTERP=1 \
    -DWAMR_BUILD_AOT=1 \
    -DWAMR_BUILD_LIBC_BUILTIN=1 \
    -DWAMR_BUILD_LIBC_WASI=1 \
    -DWAMR_BUILD_LIB_WASI_THREADS=0

cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"

# WAMR doesn't always have a proper install target; stage manually.
mkdir -p "${CVC_INSTALL_DIR}/lib" "${CVC_INSTALL_DIR}/include" "${CVC_INSTALL_DIR}/bin"

# Copy the runtime library.
find "${CVC_BUILD_DIR}" -name 'libvmlib*' -exec cp {} "${CVC_INSTALL_DIR}/lib/" \; 2>/dev/null || true
find "${CVC_BUILD_DIR}" -name 'libiwasm*' -exec cp {} "${CVC_INSTALL_DIR}/lib/" \; 2>/dev/null || true

# Copy the iwasm CLI if built.
find "${CVC_BUILD_DIR}" -name 'iwasm' -type f -executable -exec cp {} "${CVC_INSTALL_DIR}/bin/" \; 2>/dev/null || true

# Copy public headers.
cp "${CVC_SOURCE_DIR}"/core/iwasm/include/*.h "${CVC_INSTALL_DIR}/include/" 2>/dev/null || true

# Generate CMake config.
mkdir -p "${CVC_INSTALL_DIR}/lib/cmake/iwasm"
cat > "${CVC_INSTALL_DIR}/lib/cmake/iwasm/iwasmConfig.cmake" <<'EOF'
get_filename_component(_IWASM_PREFIX "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

add_library(iwasm::iwasm UNKNOWN IMPORTED)
find_library(_IWASM_LIB NAMES vmlib iwasm PATHS "${_IWASM_PREFIX}/lib" NO_DEFAULT_PATH)
set_target_properties(iwasm::iwasm PROPERTIES
    IMPORTED_LOCATION "${_IWASM_LIB}"
    INTERFACE_INCLUDE_DIRECTORIES "${_IWASM_PREFIX}/include"
)
if(UNIX)
    set_property(TARGET iwasm::iwasm APPEND PROPERTY
        INTERFACE_LINK_LIBRARIES pthread m)
endif()
set(iwasm_FOUND TRUE)
unset(_IWASM_PREFIX)
unset(_IWASM_LIB)
EOF

WAMR_VER="2.4.4"
cat > "${CVC_INSTALL_DIR}/lib/cmake/iwasm/iwasmConfigVersion.cmake" <<EOF
set(PACKAGE_VERSION "${WAMR_VER}")
if("\${PACKAGE_FIND_VERSION}" VERSION_LESS_EQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_COMPATIBLE TRUE)
    if("\${PACKAGE_FIND_VERSION}" VERSION_EQUAL PACKAGE_VERSION)
        set(PACKAGE_VERSION_EXACT TRUE)
    endif()
endif()
EOF

echo "WAMR ${WAMR_VER} staged to ${CVC_INSTALL_DIR}"
