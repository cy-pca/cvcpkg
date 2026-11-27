#!/usr/bin/env bash
# recipes/wamr/build.sh — build WAMR from source on Linux, macOS, the BSDs and
# Haiku.
#
# There is no product-mini/CMakeLists.txt, so this always builds WAMR's
# top-level CMakeLists.txt: the runtime as one static library, libiwasm.a,
# with the wasm-c-api (wasm_c_api.h) and the native API (wasm_export.h).
#
# WAMR picks its platform layer (core/shared/platform/<name>) from the host's
# name. linux, darwin and freebsd have one; OpenBSD, NetBSD and Haiku don't,
# so they build on freebsd's too, which is plain POSIX over pthreads (the
# common/posix sources). On all four:
#   - software bounds checks (WAMR_DISABLE_HW_BOUND_CHECK, which turns the
#     stack's off too): the hardware ones put guard pages under the thread's stack and
#     catch SIGSEGV, and the posix layer finds the stack's extent only on Linux
#     and macOS (os_thread_get_stack_boundary is NULL elsewhere, and the
#     thread's signal setup then fails);
#   - no SIMD: WAMR's fast interpreter runs SIMD through SIMDe, which its
#     configure clones from GitHub, and these builds stay off the network.
# OpenBSD, NetBSD and Haiku also build without WAMR's own WASI
# (WAMR_BUILD_LIBC_WASI=0): its sandboxed file layer needs posix_fallocate and
# more that OpenBSD lacks, and libcvc's guests link libcvc's WASI stubs, not
# WAMR's. NetBSD's mremap(2) isn't Linux's (no MREMAP_MAYMOVE), so the check
# that would find it is answered no, and WAMR grows a memory by mmap and copy.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

WAMR_CMAKE_DIR="${CVC_SOURCE_DIR}"
if [[ -f "${CVC_SOURCE_DIR}/product-mini/CMakeLists.txt" ]]; then
    WAMR_CMAKE_DIR="${CVC_SOURCE_DIR}/product-mini/platforms/${CVC_PLATFORM}"
fi

platform_args=()
libc_wasi=1
case "${CVC_PLATFORM}" in
    freebsd|openbsd|netbsd|haiku)
        platform_args+=(-DWAMR_BUILD_PLATFORM=freebsd -DWAMR_BUILD_SIMD=0
                        -DWAMR_DISABLE_HW_BOUND_CHECK=1)
        ;;
esac
case "${CVC_PLATFORM}" in
    openbsd|netbsd|haiku) libc_wasi=0 ;;
esac
if [[ "${CVC_PLATFORM}" == "netbsd" ]]; then
    platform_args+=(-DMREMAP_EXISTS=0)
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
    -DWAMR_BUILD_LIBC_WASI="${libc_wasi}" \
    -DWAMR_BUILD_LIB_WASI_THREADS=0 \
    ${platform_args[@]+"${platform_args[@]}"}

cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"

# WAMR doesn't always have a proper install target; stage manually.
mkdir -p "${CVC_INSTALL_DIR}/lib" "${CVC_INSTALL_DIR}/include" "${CVC_INSTALL_DIR}/bin"

# Copy the runtime library.
find "${CVC_BUILD_DIR}" -name 'libvmlib*' -exec cp {} "${CVC_INSTALL_DIR}/lib/" \; 2>/dev/null || true
find "${CVC_BUILD_DIR}" -name 'libiwasm*' -exec cp {} "${CVC_INSTALL_DIR}/lib/" \; 2>/dev/null || true

# Copy the iwasm CLI if built.  (-perm, not GNU find's -executable: the BSDs'
# find has no -executable.)
find "${CVC_BUILD_DIR}" -name 'iwasm' -type f -perm -u+x -exec cp {} "${CVC_INSTALL_DIR}/bin/" \; 2>/dev/null || true

# Copy public headers.
cp "${CVC_SOURCE_DIR}"/core/iwasm/include/*.h "${CVC_INSTALL_DIR}/include/" 2>/dev/null || true

# What a consumer of libiwasm.a links besides it: pthreads and libm, which
# Haiku has in libroot.
link_libs="pthread m"
if [[ "${CVC_PLATFORM}" == "haiku" ]]; then
    link_libs=""
fi

# Generate CMake config.
mkdir -p "${CVC_INSTALL_DIR}/lib/cmake/iwasm"
cat > "${CVC_INSTALL_DIR}/lib/cmake/iwasm/iwasmConfig.cmake" <<EOF
get_filename_component(_IWASM_PREFIX "\${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

if(NOT TARGET iwasm::iwasm)
add_library(iwasm::iwasm UNKNOWN IMPORTED)
find_library(_IWASM_LIB NAMES vmlib iwasm PATHS "\${_IWASM_PREFIX}/lib" NO_DEFAULT_PATH)
set_target_properties(iwasm::iwasm PROPERTIES
    IMPORTED_LOCATION "\${_IWASM_LIB}"
    INTERFACE_INCLUDE_DIRECTORIES "\${_IWASM_PREFIX}/include"
)
if(UNIX AND NOT "${link_libs}" STREQUAL "")
    set_property(TARGET iwasm::iwasm APPEND PROPERTY
        INTERFACE_LINK_LIBRARIES ${link_libs})
endif()
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
