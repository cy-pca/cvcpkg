#!/usr/bin/env bash
# recipes/pkg-config/build.sh — build pkg-config from source.
#
# Uses --with-internal-glib so no external glib dependency is needed.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --with-internal-glib \
    --disable-dependency-tracking \
    --disable-nls

make -j "${CVC_JOBS}"
make install
