#!/usr/bin/env bash
# recipes/libXext/build.sh — build libXext 1.3.7 with autotools.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib/pkgconfig:${CVC_BUILD_PREFIX}/share/pkgconfig}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include${CPPFLAGS:+ ${CPPFLAGS}}"
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib -Wl,-rpath,\$ORIGIN${LDFLAGS:+ ${LDFLAGS}}"

_link=(--enable-shared --disable-static)
[[ "${CVC_LINK:-shared}" == "static" ]] && _link=(--disable-shared --enable-static)

cd "${CVC_SOURCE_DIR}"
./configure --prefix="${CVC_INSTALL_DIR}" "${_link[@]}" \
    --disable-selective-werror
make -j "${CVC_JOBS}"
make install

cvc_rewrite_install_paths
