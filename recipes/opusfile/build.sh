#!/usr/bin/env bash
# recipes/opusfile/build.sh — build opusfile with autotools.
#
# The release tarball ships a generated ./configure, so no autoreconf is
# needed.  opus and ogg are located via pkg-config at configure time.
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
./configure --prefix="${CVC_INSTALL_DIR}" "${_link[@]}" \
    --disable-http \
    --disable-examples \
    --disable-doc
# The release tarball ships configure, aclocal.m4 and Makefile.in, so the build must not run
# aclocal/automake/autoconf (the host may not have them, and on the dev OpenBSD builder `make`
# decided aclocal.m4 was out of date and died with "missing: aclocal-1.16: not found", PR #580's
# build-on-dev).  Same arguments as recipes/libtool/build.sh.
_no_regen=(ACLOCAL=true AUTOCONF=true AUTOMAKE=true AUTOHEADER=true MAKEINFO=true)
make -j "${CVC_JOBS}" "${_no_regen[@]}"
make install "${_no_regen[@]}"

# Strip libtool archives (.la): they embed the dependency's absolute build-prefix
# path, which is gone after cvcpkg relocates the prefix, so a downstream libtool
# link fails.  The .so + pkg-config .pc carry the real link info.
find "${CVC_INSTALL_DIR}" -name '*.la' -delete

cvc_rewrite_install_paths
