#!/usr/bin/env bash
# recipes/gnutls/build.sh — build GnuTLS (libgnutls only) from source on Linux/BSD.
#
# Minimal TLS-backend build for ffmpeg-lgpl.  Dependencies come from the cvcpkg
# deps prefix: nettle/hogweed + libtasn1 via pkg-config, gmp via CPPFLAGS/LDFLAGS.
# The build is scoped down hard so the only artifact is the LGPL-2.1 library:
#   --disable-tools     — the src/ CLI tools are GPLv3; never build them
#   --disable-libdane   — DANE is GPLv3 and would pull in unbound
#   --without-p11-kit   — no PKCS#11 dependency
#   --with-included-unistring — use the bundled unistring, no external dep
#   --without-idn / --without-tpm* / --disable-nls / --disable-cxx
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
# nettle/libtasn1 come through pkg-config; gmp has no .pc, so expose it directly.
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"

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
    --disable-tests \
    --disable-tools \
    --disable-nls \
    --disable-cxx \
    --disable-libdane \
    --without-p11-kit \
    --without-idn \
    --without-tpm \
    --without-tpm2 \
    --with-included-unistring \
    "${shared_flags[@]}"

make -j "${CVC_JOBS}"
make install

# Normalize .pc paths; rewrite libtool dylib ids to @rpath on macOS (no-op on
# Linux/BSD, the only platforms this recipe builds on).
cvc_rewrite_install_paths
cvc_relocate_macos_dylibs
