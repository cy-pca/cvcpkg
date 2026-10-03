#!/usr/bin/env bash
# recipes/nettle/build.sh — build GNU Nettle from source on Linux/BSD.
#
# libhogweed (public-key crypto) needs gmp, so gmp's headers/libs are made
# visible via CPPFLAGS/LDFLAGS pointing at the cvcpkg deps prefix.  We build
# with --disable-assembler (portable C): it sidesteps Nettle's per-arch .asm
# path entirely, and the C code is more than fast enough for TLS traffic.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Staged build deps (m4) on PATH; gmp discoverable by the compiler/linker.
export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

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
    --disable-documentation \
    --disable-openssl \
    --disable-assembler \
    "${shared_flags[@]}"

make -j "${CVC_JOBS}"
make install

# Normalize .pc paths; rewrite libtool dylib ids to @rpath on macOS (no-op on
# Linux/BSD, the only platforms this recipe builds on).
cvc_rewrite_install_paths
cvc_relocate_macos_dylibs
