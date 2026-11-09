#!/usr/bin/env bash
# recipes/xcb-util-wm/build.sh — build xcb-util-wm 0.4.2 with autotools.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/libdata/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib/pkgconfig:${CVC_BUILD_PREFIX}/libdata/pkgconfig:${CVC_BUILD_PREFIX}/share/pkgconfig}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include${CPPFLAGS:+ ${CPPFLAGS}}"
_origin_rpath="$(cvc_make_origin_rpath)"    # assigned alone so a missing helper stops the build
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${_origin_rpath}${LDFLAGS:+ ${LDFLAGS}}"

_link=(--enable-shared --disable-static)
[[ "${CVC_LINK:-shared}" == "static" ]] && _link=(--disable-shared --enable-static)

cd "${CVC_SOURCE_DIR}"
./configure --prefix="${CVC_INSTALL_DIR}" "${_link[@]}"

# xcb-util-wm's generated Makefile uses a GNU-make idiom ($< in a non-suffix
# rule) that OpenBSD's BSD make rejects ("Using $< in a non-suffix rule context
# is a GNUmake idiom") — the other xcb-util recipes build fine with BSD make, but
# this one needs GNU make. Prefer gmake where present (OpenBSD ships it as the
# `gmake` package); harmless elsewhere since $MAKE stays `make`.
MAKE=make
if command -v gmake >/dev/null 2>&1; then MAKE=gmake; fi
"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install

# Strip libtool archives (.la): they embed the dependency's absolute build-prefix
# path, which is gone after cvcpkg relocates the prefix, so a downstream libtool
# link fails with "not a valid libtool archive". The .so + pkg-config .pc carry
# the real link info; nothing needs the .la.
find "${CVC_INSTALL_DIR}" -name '*.la' -delete

cvc_rewrite_install_paths
