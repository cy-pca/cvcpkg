#!/usr/bin/env bash
# recipes/fftw3/build-wasi.sh — cross-compile FFTW3 to wasm32-wasi via wasi-sdk.
# Two cmake passes: double precision, then single precision (float).
# Threading is disabled for wasi.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

# Build the toolchain argument set once, matching env-wasi.sh's cvc_cmake_build.
TOOLCHAIN_ARGS=()
if [[ -n "${_WASI_TOOLCHAIN}" ]]; then
    TOOLCHAIN_ARGS+=(-DCMAKE_TOOLCHAIN_FILE="${_WASI_TOOLCHAIN}")
else
    TOOLCHAIN_ARGS+=(
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

COMMON_ARGS=(
    -DBUILD_TESTS=OFF
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
    -DENABLE_THREADS=OFF
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5
    "${TOOLCHAIN_ARGS[@]}"
)

# Pass 1: double precision
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}/double" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS=OFF \
    "${COMMON_ARGS[@]}"
cmake --build "${CVC_BUILD_DIR}/double" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}/double"

# Pass 2: single precision (float)
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}/float" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS=OFF \
    -DENABLE_FLOAT=ON \
    "${COMMON_ARGS[@]}"
cmake --build "${CVC_BUILD_DIR}/float" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}/float"

# Rewrite .pc files so prefix/libdir/includedir are relative to ${pcfiledir}.
for pc in "${CVC_INSTALL_DIR}"/lib/pkgconfig/fftw3*.pc; do
    [ -f "$pc" ] || continue
    sed -i.bak \
        -e 's|^prefix=.*|prefix=${pcfiledir}/../..|' \
        -e 's|^exec_prefix=.*|exec_prefix=${prefix}|' \
        -e 's|^libdir=.*|libdir=${prefix}/lib|' \
        -e 's|^includedir=.*|includedir=${prefix}/include|' \
        "$pc"
    rm -f "${pc}.bak"
done
