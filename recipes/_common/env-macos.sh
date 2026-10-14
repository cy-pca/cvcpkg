#!/usr/bin/env bash
# recipes/_common/env-macos.sh — shared environment for macOS recipe builds.
set -euo pipefail

_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "${_COMMON_DIR}/rewrite-install-paths.sh"

: "${CVC_BUILD_TYPE:=Release}"
: "${CVC_LINK:=shared}"
: "${CVC_JOBS:=$(sysctl -n hw.ncpu 2>/dev/null || echo 4)}"
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
: "${MACOSX_DEPLOYMENT_TARGET:=13.0}"

# The system (Apple) compiler, by absolute path -- never a bare `clang`.  The
# builder puts the build prefix's and deps prefix's bin/ ahead of /usr/bin on
# PATH, and cvcpkg's llvm, llvm18 and llvm20 bundles ship a `clang`.  With one
# of them in the prefix -- a recipe that depends on it, or any recipe a pack-all
# shard builds after it -- a bare name compiles with cvcpkg's clang instead, and
# links with ld64.lld (llvm and llvm18 build clang with lld as its default
# linker).  xcrun follows DEVELOPER_DIR / xcode-select; /usr/bin/clang is its
# shim.  An explicit CC / CXX still wins.
_cvc_apple_tool() { xcrun -f "$1" 2>/dev/null || echo "/usr/bin/$1"; }
export CC="${CC:-$(_cvc_apple_tool clang)}"
export CXX="${CXX:-$(_cvc_apple_tool clang++)}"
export MACOSX_DEPLOYMENT_TARGET

# `xcrun -f clang` is the toolchain's clang by absolute path, which -- unlike the
# /usr/bin/clang shim -- does not locate the macOS SDK by itself on every Xcode:
# on Xcode 26.6 (GitHub macos-latest) every C extension failed with
# "Python.h: fatal error: 'assert.h' file not found" (cython-cp313, 2026-10-14).
# Export SDKROOT, which clang honours; an explicit SDKROOT still wins.
if [ -z "${SDKROOT:-}" ]; then
    SDKROOT="$(xcrun --sdk macosx --show-sdk-path 2>/dev/null || true)"
    [ -n "${SDKROOT}" ] && export SDKROOT
fi

# clang 16+ promotes several legacy-C diagnostics to hard errors by default
# (-Werror=implicit-function-declaration, -Werror=implicit-int,
# -Werror=int-conversion).  The Xcode 26.5 / clang 21 toolchain on the current
# macOS runners then fails many pre-C99 autotools packages: their configure
# probes misdetect features and old sources won't compile.  Relax those back to
# warnings so legacy code builds as it did on older clang.  We APPEND, so
# recipe- and configure-supplied flags still take effect.
_macos_legacy_c_compat="-Wno-implicit-function-declaration -Wno-implicit-int -Wno-int-conversion"
export CFLAGS="${CFLAGS:-} ${_macos_legacy_c_compat}"

_build_type_lc=$(echo "$CVC_BUILD_TYPE" | tr '[:upper:]' '[:lower:]')
case "$_build_type_lc" in
    release) CMAKE_BUILD_TYPE=Release  ;;
    debug)   CMAKE_BUILD_TYPE=Debug    ;;
    *)       CMAKE_BUILD_TYPE=Release  ;;
esac

if [[ "${CVC_LINK}" == "static" ]]; then
    BUILD_SHARED_LIBS=OFF
else
    BUILD_SHARED_LIBS=ON
fi

if [[ -n "${CVC_DEPS_PREFIX:-}" ]]; then
    export CMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}"
fi

cvc_cmake_build() {
    cmake -G Ninja \
        -S "${CVC_SOURCE_DIR}" \
        -B "${CVC_BUILD_DIR}" \
        -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
        -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
        -DBUILD_SHARED_LIBS="${BUILD_SHARED_LIBS}" \
        -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
        -DCMAKE_CXX_STANDARD=17 \
        -DCMAKE_OSX_DEPLOYMENT_TARGET="${MACOSX_DEPLOYMENT_TARGET}" \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DCMAKE_INSTALL_RPATH="@loader_path;@loader_path/../lib" \
        -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON \
        "$@"
    cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
    cmake --install "${CVC_BUILD_DIR}"
    cvc_rewrite_install_paths
}

echo "── env-macos.sh loaded ──"
echo "  CC=${CC}  CXX=${CXX}"
echo "  DEPLOYMENT_TARGET=${MACOSX_DEPLOYMENT_TARGET}  SDKROOT=${SDKROOT:-}"
echo "  BUILD_TYPE=${CMAKE_BUILD_TYPE}  LINK=${CVC_LINK}  JOBS=${CVC_JOBS}"
