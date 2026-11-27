#!/usr/bin/env bash
# recipes/wamr/build.sh — build WAMR from source on Linux, macOS, the BSDs and
# Haiku.
#
# There is no product-mini/CMakeLists.txt, so this always builds WAMR's
# top-level CMakeLists.txt: the runtime as one static library, libiwasm.a,
# with the wasm-c-api (wasm_c_api.h) and the native API (wasm_export.h).
#
# The proposals WebAssembly guests of libcvc's use (CVC_WASM_GUEST_FEATURES in
# libcvc's src/ariadne_wasm/ariadne_wasm.cmake) are on everywhere, named rather
# than left to each platform's defaults: reference types (which WAMR's windows
# build leaves off), bulk memory, tail calls and extended constants (off by
# default everywhere).
#
# No WASI of WAMR's own on any platform (WAMR_BUILD_LIBC_WASI=0, as of
# cvc.4). A host embedding WAMR defines a guest's imports itself, through the
# wasm-c-api, and WAMR's WASI takes every wasi_snapshot_preview1 import before
# the host's: its loader insists that such a module export a memory, and its
# instantiation calls the module's _initialize, before the host's imports are
# wired. libcvc's guests link libcvc's own WASI stubs.
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
# NetBSD's mremap(2) isn't Linux's (no MREMAP_MAYMOVE), so the check
# that would find it is answered no, and WAMR grows a memory by mmap and copy.
# The three have no MAP_32BIT either, which WAMR's mmap passes on x86_64, and
# NetBSD's <strings.h> has a popcount32 and popcount64 of its own.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

WAMR_CMAKE_DIR="${CVC_SOURCE_DIR}"
if [[ -f "${CVC_SOURCE_DIR}/product-mini/CMakeLists.txt" ]]; then
    WAMR_CMAKE_DIR="${CVC_SOURCE_DIR}/product-mini/platforms/${CVC_PLATFORM}"
fi

platform_args=()
case "${CVC_PLATFORM}" in
    freebsd|openbsd|netbsd|haiku)
        platform_args+=(-DWAMR_BUILD_PLATFORM=freebsd -DWAMR_BUILD_SIMD=0
                        -DWAMR_DISABLE_HW_BOUND_CHECK=1)
        ;;
esac
if [[ "${CVC_PLATFORM}" == "netbsd" ]]; then
    platform_args+=(-DMREMAP_EXISTS=0)
    # NetBSD's <strings.h> declares popcount32 and popcount64, and WAMR's
    # interpreters define static functions of those names (dev job 25601): theirs
    # are renamed, in the two files that define and use them.
    for f in core/iwasm/interpreter/wasm_interp_fast.c core/iwasm/interpreter/wasm_interp_classic.c; do
        sed -e 's/popcount32/wamr_popcount32/g' -e 's/popcount64/wamr_popcount64/g' \
            "${CVC_SOURCE_DIR}/${f}" > "${CVC_SOURCE_DIR}/${f}.netbsd"
        mv "${CVC_SOURCE_DIR}/${f}.netbsd" "${CVC_SOURCE_DIR}/${f}"
    done
fi
# The posix layer asks mmap for MAP_32BIT on x86_64 (a hint the AOT loader's first try passes,
# which falls back without it); OpenBSD, NetBSD and Haiku have no such flag.
case "${CVC_PLATFORM}" in
    openbsd|netbsd|haiku) export CFLAGS="${CFLAGS:-} -DMAP_32BIT=0" ;;
esac

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
    -DWAMR_BUILD_LIBC_WASI=0 \
    -DWAMR_BUILD_LIB_WASI_THREADS=0 \
    -DWAMR_BUILD_REF_TYPES=1 \
    -DWAMR_BUILD_BULK_MEMORY=1 \
    -DWAMR_BUILD_TAIL_CALL=1 \
    -DWAMR_BUILD_EXTENDED_CONST_EXPR=1 \
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
