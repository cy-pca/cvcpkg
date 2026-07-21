#!/usr/bin/env bash
# recipes/mpfr/build.sh — build MPFR from source on Linux and macOS.
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
    --with-gmp="${CVC_DEPS_PREFIX}" \
    "${shared_flags[@]}"

make -j "${CVC_JOBS}"
make install

# Same autotools/libtool relocatability handling as gmp: normalize .pc/.cmake
# text paths, then rewrite the baked-in temp $CVC_INSTALL_DIR dylib ids/deps
# (libmpfr, plus its dependency on libgmp) to @rpath for macOS.
cvc_rewrite_install_paths
cvc_relocate_macos_dylibs
