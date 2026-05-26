#!/usr/bin/env bash
# recipes/cmake/build.sh — bootstrap CMake from source on Linux and macOS.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# CMake bootstraps itself — no pre-existing cmake required.
./bootstrap \
    --prefix="${CVC_INSTALL_DIR}" \
    --parallel="${CVC_JOBS}" \
    -- \
    -DCMAKE_USE_OPENSSL=OFF

make -j "${CVC_JOBS}"
make install
