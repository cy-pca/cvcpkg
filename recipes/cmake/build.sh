#!/usr/bin/env bash
# recipes/cmake/build.sh — bootstrap CMake from source on Linux and macOS.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# Build with system curl so cmake's file(DOWNLOAD) supports HTTPS.
# Our curl recipe is built before cmake (autotools-based, no cmake needed).
BOOTSTRAP_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --parallel="${CVC_JOBS}"
    --system-curl
)

CMAKE_FLAGS=(
    -DCMAKE_USE_OPENSSL=ON
)

if [[ -n "${CVC_DEPS_PREFIX:-}" ]]; then
    CMAKE_FLAGS+=(-DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}")
    export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
    export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

./bootstrap "${BOOTSTRAP_ARGS[@]}" -- "${CMAKE_FLAGS[@]}"

make -j "${CVC_JOBS}"
make install
