#!/usr/bin/env bash
# recipes/make/build.sh — build GNU Make from source.
#
# GNU Make 4.4.1 configures and builds with an existing make (the host
# bootstrap toolchain), the same way the other autotools host-tool
# recipes (m4, autoconf, ...) do. The resulting bin/make is what
# downstream recipes then use once CVC_INSTALL_DIR/bin is on PATH.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --disable-nls

make -j "${CVC_JOBS}"
make install
