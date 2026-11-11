#!/usr/bin/env bash
# recipes/llvm20/build.sh — build LLVM 20 core from the monorepo.
#
# The LLVM monorepo source unpacks as a flat directory containing llvm/,
# clang/, lld/, compiler-rt/, etc.  The CMake root is llvm/.
#
# Unlike recipes/llvm, LLVM_ENABLE_PROJECTS is left empty: the only consumer
# (llvm-cbe) links LLVM's libraries and references neither clang nor lld, so
# building them would roughly double the build for nothing.
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

# On the BSDs env-<bsd>.sh adds the ports/pkgsrc include and lib dirs to the
# compiler flags, and NetBSD's also bakes -R/usr/pkg/lib into every link, ahead
# of $ORIGIN (cvc.4's NetBSD bundle: RUNPATH /usr/pkg/lib:$ORIGIN:$ORIGIN/../lib
# on all 72 objects).  A host zstd or libLLVM there could shadow the packaged
# one, so drop those dirs for this build, as recipes/mesa does.
case "${CVC_PLATFORM}" in
    freebsd|netbsd)
        _drop_host_dirs() {
            local _w _out=""
            for _w in $1; do
                case "${_w}" in
                    -I/usr/local/include|-I/usr/pkg/include|-L/usr/local/lib|-L/usr/pkg/lib|-Wl,-R/usr/pkg/lib) ;;
                    *) _out="${_out:+${_out} }${_w}" ;;
                esac
            done
            printf '%s' "${_out}"
        }
        CFLAGS="$(_drop_host_dirs "${CFLAGS:-}")"
        CXXFLAGS="$(_drop_host_dirs "${CXXFLAGS:-}")"
        LDFLAGS="$(_drop_host_dirs "${LDFLAGS:-}")"
        export CFLAGS CXXFLAGS LDFLAGS
        ;;
esac

# LLVM_PARALLEL_LINK_JOBS: each full link of an LLVM tool can use several GB
# of RAM.  Limit simultaneous link jobs to avoid OOM.
_link_jobs=$(( CVC_JOBS > 4 ? 2 : 1 ))

# Optional host libraries.  LLVM probes for each of these with plain
# find_library/find_package and links whatever the builder happens to have, so
# an installed port or distro package silently becomes a runtime dependency of
# the published bundle.  cvc.4's FreeBSD libLLVM.so.20.1 needed ports
# libedit.so.0 (/usr/local/lib; the base system's is libedit.so.8), so libLLVM,
# and everything that loads it (mesa's llvmpipe, drjit), did not load on a
# FreeBSD host without that port.  Each switch is pinned:
#   zlib, zstd     FORCE_ON: cvcpkg's own (declared in recipe.yaml), found
#                  first through CMAKE_PREFIX_PATH.
#   libedit        OFF: only LLVMLineEditor uses it, and nothing in an
#                  LLVM-only build calls that (clang-repl is clang's).  Also
#                  stops LLVMConfig.cmake from making consumers find_package
#                  LibEdit.
#   libxml2        OFF: only the Windows manifest merger (llvm-mt).
#   libpfm         OFF: only llvm-exegesis's hardware-counter backend.
#   FFI            OFF: only the interpreter's external calls (lli -force-interpreter).
#   curl, httplib  OFF: llvm-debuginfod's HTTP client and server.
#   Z3             OFF: only the clang static analyzer's solver (clang is not built).
# LLVM 19 removed terminfo support, so there is no LLVM_ENABLE_TERMINFO to set.
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
    -DLLVM_TARGETS_TO_BUILD="X86;AArch64;WebAssembly" \
    -DLLVM_BUILD_LLVM_DYLIB=ON \
    -DLLVM_LINK_LLVM_DYLIB=ON \
    -DLLVM_INCLUDE_TESTS=OFF \
    -DLLVM_INCLUDE_BENCHMARKS=OFF \
    -DLLVM_INCLUDE_EXAMPLES=OFF \
    -DLLVM_ENABLE_ZLIB=FORCE_ON \
    -DLLVM_ENABLE_ZSTD=FORCE_ON \
    -DLLVM_ENABLE_LIBEDIT=OFF \
    -DLLVM_ENABLE_LIBXML2=OFF \
    -DLLVM_ENABLE_LIBPFM=OFF \
    -DLLVM_ENABLE_FFI=OFF \
    -DLLVM_ENABLE_CURL=OFF \
    -DLLVM_ENABLE_HTTPLIB=OFF \
    -DLLVM_ENABLE_Z3_SOLVER=OFF \
    -DLLVM_ENABLE_BINDINGS=OFF \
    -DLLVM_PARALLEL_LINK_JOBS="${_link_jobs}"

cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"
