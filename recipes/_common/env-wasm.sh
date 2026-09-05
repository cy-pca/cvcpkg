#!/usr/bin/env bash
# recipes/_common/env-wasm.sh — shared environment for wasm cross-compilation.
#
# Sourced by build-wasm.sh scripts.  Loads the host platform env first
# (for the native compiler/cmake), then activates Emscripten and
# overrides the cmake helper to use the Emscripten toolchain file.
set -euo pipefail

# Load the native host environment (compiler, cmake helpers, etc.).
_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_host="${CVC_HOST_PLATFORM:-linux}"
source "${_COMMON_DIR}/env-${_host}.sh"

: "${CVC_EMSDK_DIR:?CVC_EMSDK_DIR must point to the activated emsdk bundle}"

# Activate Emscripten.
# shellcheck disable=SC1091
_cvc_em_cache_pre="${EM_CACHE:-}"  # emsdk_env.sh clears it; restored below
# Source emsdk_env.sh FROM INSIDE the emsdk dir.  The fleet builders
# symlink-merge the toolchain into the build prefix, so emsdk_env.sh is an
# ABSOLUTE symlink there -- and emsdk_env.sh's own symlink self-location is
# broken for absolute targets (it prepends $DIR to the absolute path, the cd
# fails, and it only finds emsdk.py if the CWD already contains it).  Sourcing
# with CVC_EMSDK_DIR as the CWD is emsdk's documented workaround ("source this
# script while in the 'emsdk' directory") and makes that accidental fallback
# deterministic.  catx-03 never hit this because its emsdk is a real extracted
# tree, not symlinks.  Restore the CWD afterwards -- the recipe build runs next
# and expects it.
_cvc_em_prev_pwd="$PWD"
cd "${CVC_EMSDK_DIR}"
source "${CVC_EMSDK_DIR}/emsdk_env.sh"
cd "${_cvc_em_prev_pwd}"
unset _cvc_em_prev_pwd
# emcc must WRITE to its cache (lock files under cache/symbol_lists at link
# time, system libs on first use). On a shared builder the emsdk can belong
# to another account (catx-03: /opt/cvc-wasm/emsdk is owned by github-runner
# while the libcvc-deps runner is tfx) and every em++ link then dies with
# PermissionError on the .json.lock. emsdk_env.sh UNSETS EM_CACHE when it is
# sourced, so this must run after it: restore an explicit EM_CACHE, else
# fall back to a per-user cache when the emsdk cache is read-only.
_cvc_em_cache_dir="${CVC_EMSDK_DIR}/upstream/emscripten/cache"
if [[ -n "${_cvc_em_cache_pre:-}" ]]; then
    export EM_CACHE="${_cvc_em_cache_pre}"
elif [[ -d "${_cvc_em_cache_dir}" && ! -w "${_cvc_em_cache_dir}" ]]; then
    export EM_CACHE="${HOME}/.cache/emscripten-cvcpkg"
    mkdir -p "${EM_CACHE}"
    echo "cvcpkg: emsdk cache is read-only for $(id -un); using EM_CACHE=${EM_CACHE}" >&2
fi

# Wasm builds are always static.
BUILD_SHARED_LIBS=OFF
CVC_LINK=static
export CVC_LINK

# Opt-in threaded (SharedArrayBuffer) variant: CVC_WASM_THREADS=1 compiles the
# whole closure with -pthread. Emscripten forbids mixing -pthread and
# non-pthread objects, so a threaded consumer needs EVERY static lib in its
# link built this way (into its own prefix). Hosting then requires COOP/COEP.
if [[ "${CVC_WASM_THREADS:-0}" == "1" ]]; then
    export CFLAGS="-pthread ${CFLAGS:-}"
    export CXXFLAGS="-pthread ${CXXFLAGS:-}"
    export LDFLAGS="-pthread ${LDFLAGS:-}"
    echo "── CVC_WASM_THREADS=1: building with -pthread ──"
fi

# Re-define cvc_cmake_build to inject the Emscripten toolchain file.
cvc_cmake_build() {
    local _find_root_path_args=()
    if [[ -n "${CVC_DEPS_PREFIX:-}" ]]; then
        _find_root_path_args+=(-DCMAKE_FIND_ROOT_PATH="${CVC_DEPS_PREFIX}")
    fi
    cmake -G Ninja \
        -S "${CVC_SOURCE_DIR}" \
        -B "${CVC_BUILD_DIR}" \
        -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
        -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
        -DBUILD_SHARED_LIBS=OFF \
        -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
        -DCMAKE_CXX_STANDARD=17 \
        -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
        -DCMAKE_TOOLCHAIN_FILE="${EMSDK}/upstream/emscripten/cmake/Modules/Platform/Emscripten.cmake" \
        ${_find_root_path_args[@]+"${_find_root_path_args[@]}"} \
        "$@"
    cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
    cmake --install "${CVC_BUILD_DIR}"
    cvc_rewrite_install_paths
}

echo "── env-wasm.sh loaded (host=${_host}) ──"
echo "  EMSDK=${EMSDK}"
echo "  BUILD_TYPE=${CMAKE_BUILD_TYPE}  LINK=static  JOBS=${CVC_JOBS}"
