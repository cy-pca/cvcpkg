#!/usr/bin/env bash
# recipes/llvm/build.sh — build LLVM + Clang + LLD from the monorepo.
#
# The LLVM monorepo source unpacks as a flat directory containing llvm/,
# clang/, lld/, compiler-rt/, etc.  The CMake root is llvm/ and
# LLVM_ENABLE_PROJECTS pulls in the sibling sub-projects.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

: "${CVC_INSTALL_DIR:?}"
: "${CVC_SOURCE_DIR:?}"
: "${CVC_BUILD_DIR:?}"
: "${CVC_DEPS_PREFIX:?}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

# Sanity: ensure the llvm/ subdirectory is present.
if [[ ! -d "${CVC_SOURCE_DIR}/llvm" ]]; then
    echo "cvcpkg: error: ${CVC_SOURCE_DIR}/llvm not found" >&2
    exit 1
fi

# Determine RPATH for the installed shared library.
case "${CVC_PLATFORM}" in
    macos)   _rpath="@loader_path;@loader_path/../lib" ;;
    *)       _rpath="\$ORIGIN;\$ORIGIN/../lib" ;;
esac

# LLVM_PARALLEL_LINK_JOBS: each LTO/full link of an LLVM tool can use
# several GB of RAM.  Limit simultaneous link jobs to avoid OOM.
_link_jobs=$(( CVC_JOBS > 4 ? 2 : 1 ))

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}/llvm" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_CXX_STANDARD=17 \
    -DCMAKE_INSTALL_RPATH="${_rpath}" \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DLLVM_ENABLE_PROJECTS="clang;lld" \
    -DLLVM_TARGETS_TO_BUILD="X86;AArch64;WebAssembly" \
    -DLLVM_BUILD_LLVM_DYLIB=ON \
    -DLLVM_LINK_LLVM_DYLIB=ON \
    -DLLVM_INCLUDE_TESTS=OFF \
    -DLLVM_INCLUDE_BENCHMARKS=OFF \
    -DLLVM_INCLUDE_EXAMPLES=OFF \
    -DLLVM_ENABLE_ZLIB=FORCE_ON \
    -DLLVM_ENABLE_LIBXML2=OFF \
    -DLLVM_ENABLE_TERMINFO=ON \
    -DLLVM_ENABLE_BINDINGS=OFF \
    -DLLVM_PARALLEL_LINK_JOBS="${_link_jobs}" \
    -DCLANG_INCLUDE_DOCS=OFF \
    -DCLANG_INCLUDE_TESTS=OFF \
    -DCLANG_BUILD_TOOLS=ON \
    -DCLANG_DEFAULT_LINKER=lld

cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"
