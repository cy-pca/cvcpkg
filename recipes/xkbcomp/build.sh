#!/usr/bin/env bash
# recipes/xkbcomp/build.sh — build the xkbcomp binary 1.5.0 with autotools.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/libdata/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib/pkgconfig:${CVC_BUILD_PREFIX}/libdata/pkgconfig:${CVC_BUILD_PREFIX}/share/pkgconfig}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include${CPPFLAGS:+ ${CPPFLAGS}}"
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib${LDFLAGS:+ ${LDFLAGS}}"

cd "${CVC_SOURCE_DIR}"
# xkbcomp is always invoked by the X server with an explicit -R<xkb root>, so
# the configured default root is only a fallback.  Use the conventional system
# path rather than baking this build's temporary install prefix into the binary.
./configure --prefix="${CVC_INSTALL_DIR}" \
    --with-xkb-config-root=/usr/share/X11/xkb \
    --disable-selective-werror
make -j "${CVC_JOBS}"
make install

find "${CVC_INSTALL_DIR}" -name '*.la' -delete

# Relocatable: executables find the cvcpkg libs next to the bin/ dir, wherever
# the prefix is moved to.
if command -v patchelf >/dev/null 2>&1; then
    patchelf --set-rpath '$ORIGIN/../lib' "${CVC_INSTALL_DIR}/bin/xkbcomp"
else
    echo "cvcpkg: patchelf not found — xkbcomp RUNPATH not relocatable" >&2
    exit 1
fi

cvc_rewrite_install_paths
