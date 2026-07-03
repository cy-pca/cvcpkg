#!/usr/bin/env bash
# recipes/gmp/build.sh — build GMP from source on Linux and macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

shared_flags=()
if [[ "${CVC_LINK}" == "static" ]]; then
    shared_flags+=(--disable-shared --enable-static)
else
    shared_flags+=(--enable-shared --enable-static)
fi

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --enable-cxx \
    "${shared_flags[@]}"

make -j "${CVC_JOBS}"
make install

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
