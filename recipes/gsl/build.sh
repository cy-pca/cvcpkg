#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# GSL uses autotools upstream; we configure + make + install.
cd "${CVC_SOURCE_DIR}"
./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --enable-shared \
    --enable-static \
    --with-pic \
    CFLAGS="${CFLAGS:-"-O2 -fPIC"}"
make -j "${CVC_JOBS}"
make install

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
