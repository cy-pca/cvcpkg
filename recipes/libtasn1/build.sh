#!/usr/bin/env bash
# recipes/libtasn1/build.sh — build GNU libtasn1 (ASN.1 library) from source.
#
# Plain GNU autotools, no external dependencies.  We build only the shared
# library (--disable-doc skips the GPLv3 manual and its texinfo requirement),
# which is LGPL-2.1-or-later — the license this recipe advertises.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
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
    --libdir="${CVC_INSTALL_DIR}/lib" \
    --disable-doc \
    "${shared_flags[@]}"

make -j "${CVC_JOBS}"
make install

# Normalize .pc paths, then rewrite libtool's baked-in temp dylib ids to
# @rpath on macOS (no-op elsewhere; this recipe only builds on Linux/BSD).
cvc_rewrite_install_paths
cvc_relocate_macos_dylibs
