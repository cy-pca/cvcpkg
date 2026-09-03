#!/usr/bin/env bash
# recipes/openmp/build.sh — LLVM's OpenMP runtime (libomp), built standalone.
#
# Why this recipe exists: AppleClang ships no OpenMP runtime, so
# find_package(OpenMP) fails on macOS unless a libomp is already on the prefix.
# libcvc declares `option(CVC_ENABLE_OPENMP ... ON)` and then SILENTLY clears it
# when the runtime is missing, so macOS builds were quietly serialising every
# `#pragma omp` site with nothing in the log to say so.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

LLVM_VER="21.1.8"
# LLVM stopped publishing per-project source tarballs after 21.x: from 22.x the
# only source asset is the ~159 MB monorepo. Staying on 21.1.8 keeps this a
# 1 MB download. Revisit deliberately, not by reflex -- moving to 22+ means
# either the monorepo or a different source strategy.
CMAKE_TARBALL_SHA256="85735f20fd8c81ecb0a09abb0c267018475420e93b65050cc5b7634eab744de9"
CMAKE_TARBALL_URL="https://github.com/llvm/llvm-project/releases/download/llvmorg-${LLVM_VER}/cmake-${LLVM_VER}.src.tar.xz"

# Upstream's openmp/CMakeLists.txt line 4 does
#   set(LLVM_COMMON_CMAKE_UTILS ${CMAKE_CURRENT_SOURCE_DIR}/../cmake)
# and then includes modules from there (runtime/cmake/config-ix.cmake needs
# LLVMCheckCompilerLinkerFlag). That is a plain set(), so it is NOT overridable
# with -D: the LLVM common cmake utilities must sit in a directory literally
# named `cmake` NEXT TO the openmp source. Without it, configure dies with
#   config-ix.cmake:20 (include): include could not find requested file
# A cvcpkg `source:` block fetches exactly one tarball, so the second is
# fetched here -- the same pinned-curl idiom recipes/grpc and recipes/ca-bundle
# already use for their secondary downloads.
STAGE="${CVC_BUILD_DIR}/llvm-src"
rm -rf "${STAGE}"
mkdir -p "${STAGE}/cmake"

# Reproduce the monorepo layout: openmp/ beside cmake/.
cp -R "${CVC_SOURCE_DIR}" "${STAGE}/openmp"

TARBALL="${CVC_BUILD_DIR}/cmake-${LLVM_VER}.src.tar.xz"
echo "cvcpkg: downloading ${CMAKE_TARBALL_URL} ..."
curl -fsSL --retry 5 --retry-delay 3 -o "${TARBALL}" "${CMAKE_TARBALL_URL}"

# sha256 tooling differs by build host: coreutils sha256sum on Linux, sha256(1)
# on the BSDs, shasum/openssl on macOS. Same fallback chain as recipes/grpc.
if command -v sha256sum >/dev/null 2>&1; then
    ACTUAL_SHA256="$(sha256sum "${TARBALL}" | awk '{print $1}')"
elif command -v sha256 >/dev/null 2>&1; then
    ACTUAL_SHA256="$(sha256 -q "${TARBALL}")"
elif command -v shasum >/dev/null 2>&1; then
    ACTUAL_SHA256="$(shasum -a 256 "${TARBALL}" | awk '{print $1}')"
else
    ACTUAL_SHA256="$(openssl dgst -sha256 "${TARBALL}" | awk '{print $NF}')"
fi
if [[ "${ACTUAL_SHA256}" != "${CMAKE_TARBALL_SHA256}" ]]; then
    echo "ERROR: cmake-${LLVM_VER}.src.tar.xz sha256 mismatch" >&2
    echo "       expected ${CMAKE_TARBALL_SHA256}" >&2
    echo "       actual   ${ACTUAL_SHA256}" >&2
    exit 1
fi
tar xf "${TARBALL}" -C "${STAGE}/cmake" --strip-components=1

# libomp links nothing out of the prefix, but keep the fleet's rpath convention
# so the artifact looks like every other bundle.
CMAKE_ARGS=(
    -G Ninja
    -S "${STAGE}/openmp"
    -B "${CVC_BUILD_DIR}/omp-build"
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}"
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}"
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON
    -DOPENMP_STANDALONE_BUILD=ON
    -DLIBOMP_ENABLE_SHARED="${BUILD_SHARED_LIBS}"
    # LIBOMP_INSTALL_ALIASES=OFF is load-bearing, not tidiness. The aliases are
    # lib/libgomp.so{,.1} and lib/libiomp5.so, and CMake's FindOpenMP searches
    # `NAMES omp gomp iomp5` through CMAKE_PREFIX_PATH. Shipping a libgomp
    # alias into a shared prefix invites a GCC-built consumer to bind OUR
    # runtime beside the real libgomp -- the classic "OMP: Error #15:
    # Initializing libomp, but found libgomp already initialized" abort.
    -DLIBOMP_INSTALL_ALIASES=OFF
    # Runtime only. libomptarget pulls in offloading plugins (CUDA/AMDGPU) that
    # nothing here consumes; ompd drags in a gdb python tree under share/.
    -DOPENMP_ENABLE_LIBOMPTARGET=OFF
    -DOPENMP_ENABLE_OMPT_TOOLS=OFF
    -DLIBOMP_OMPD_SUPPORT=OFF
)

case "${CVC_PLATFORM}" in
    macos)
        CMAKE_ARGS+=(
            -DCMAKE_OSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET}"
            -DCMAKE_INSTALL_RPATH="@loader_path;@loader_path/../lib"
        )
        ;;
    *)
        CMAKE_ARGS+=(-DCMAKE_INSTALL_RPATH='$ORIGIN')
        ;;
esac

cmake "${CMAKE_ARGS[@]}"
cmake --build "${CVC_BUILD_DIR}/omp-build" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}/omp-build"

# Prove the artifact is the one consumers need, at BUILD time rather than in a
# consumer's configure months later. find_package(OpenMP) locates the runtime
# with `find_library(NAMES omp gomp iomp5)` and then compiles a test TU that
# includes <omp.h>, so a bundle missing either half is useless -- and this
# recipe exists precisely because that failure is silent downstream.
_omp_lib=$(ls "${CVC_INSTALL_DIR}"/lib/libomp.* 2>/dev/null | head -1 || true)
if [[ -z "${_omp_lib}" ]]; then
    echo "ERROR: no lib/libomp.* installed into ${CVC_INSTALL_DIR}" >&2
    ls -la "${CVC_INSTALL_DIR}/lib" >&2 || true
    exit 1
fi
if [[ ! -f "${CVC_INSTALL_DIR}/include/omp.h" ]]; then
    echo "ERROR: include/omp.h missing from ${CVC_INSTALL_DIR}" >&2
    exit 1
fi
# The aliases must NOT be here (see LIBOMP_INSTALL_ALIASES above).
for _alias in libgomp libiomp5; do
    if ls "${CVC_INSTALL_DIR}"/lib/${_alias}.* >/dev/null 2>&1; then
        echo "ERROR: ${_alias} alias installed -- it would collide with a real" >&2
        echo "       GCC libgomp in a shared prefix. Check LIBOMP_INSTALL_ALIASES." >&2
        exit 1
    fi
done
echo "openmp: OK -- $(basename "${_omp_lib}") + include/omp.h, no gomp/iomp5 aliases"

cvc_rewrite_install_paths
