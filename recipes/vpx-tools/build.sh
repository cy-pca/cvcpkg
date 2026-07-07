#!/usr/bin/env bash
# recipes/vpx-tools/build.sh — build vpxenc and vpxdec VP8/VP9 tools.
#
# Built from the same source tarball as the libvpx recipe.
# libvpx must be pre-built and available in CVC_DEPS_PREFIX.
# Only the vpxenc and vpxdec binaries are installed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"

mkdir -p "${CVC_BUILD_DIR}"
cd "${CVC_BUILD_DIR}"

MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        command -v gmake >/dev/null 2>&1 && MAKE=gmake
        ;;
esac

"${CVC_SOURCE_DIR}/configure" \
    --prefix="${CVC_INSTALL_DIR}" \
    --enable-tools \
    --disable-examples \
    --disable-docs \
    --disable-unit-tests \
    --enable-vp8 \
    --enable-vp9 \
    --disable-shared \
    --disable-static \
    --extra-cflags="${CPPFLAGS:-}" \
    --extra-ldflags="${LDFLAGS:-} -lvpx"

"${MAKE}" -j "${CVC_JOBS}" vpxenc vpxdec

mkdir -p "${CVC_INSTALL_DIR}/bin"
install -m 755 vpxenc "${CVC_INSTALL_DIR}/bin/vpxenc"
install -m 755 vpxdec "${CVC_INSTALL_DIR}/bin/vpxdec"
