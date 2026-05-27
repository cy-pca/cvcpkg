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
    # Embed RPATH so the cmake binary (and any helpers) can find
    # recipe-built shared libs (libcurl, libssl) at build-time AND
    # install-time without relying on LD_LIBRARY_PATH alone.
    CMAKE_FLAGS+=(-DCMAKE_BUILD_RPATH="${CVC_DEPS_PREFIX}/lib")
    CMAKE_FLAGS+=(-DCMAKE_INSTALL_RPATH="${CVC_DEPS_PREFIX}/lib")
    export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
    export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi

./bootstrap "${BOOTSTRAP_ARGS[@]}" -- "${CMAKE_FLAGS[@]}"

make -j "${CVC_JOBS}"

# Debug: verify libssl and libcurl are discoverable before install
echo "=== cmake build.sh: LD_LIBRARY_PATH=${LD_LIBRARY_PATH:-<unset>}"
if [[ -n "${CVC_DEPS_PREFIX:-}" ]]; then
    echo "=== cmake build.sh: CVC_DEPS_PREFIX=${CVC_DEPS_PREFIX}"
    ls -la "${CVC_DEPS_PREFIX}/lib"/libssl* "${CVC_DEPS_PREFIX}/lib"/libcurl* 2>/dev/null || echo "=== WARNING: libssl/libcurl not found in prefix/lib"
    echo "=== cmake build.sh: checking bin/cmake dynamic deps:"
    ldd bin/cmake 2>/dev/null | grep -E "ssl|curl|crypto" || true
fi

make install
