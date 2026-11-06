#!/usr/bin/env bash
# recipes/vorbis-tools/build.sh — build Ogg Vorbis command-line tools.
#
# Builds ogg123, oggenc, oggdec, ogginfo, vorbiscomment.
# libogg and libvorbis must be available in CVC_DEPS_PREFIX.
# ogg123 audio output is disabled (no dependency on libao) to keep
# the build self-contained; it can still decode to stdout.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"

cd "${CVC_SOURCE_DIR}"

# OpenBSD: no iconv.  Its libc has none, so configure linked the build host's
# ports libiconv.so.7.1 from /usr/local/lib (env-openbsd.sh), and cvc.5's
# oggdec, oggenc, ogginfo and vorbiscomment NEEDed it undeclared.  Without
# HAVE_ICONV the tools use their own converter (share/charset.c), which knows
# UTF-8, US-ASCII, ISO-8859-1 and -2: enough for OpenBSD's only locales, C and
# UTF-8.
CONFIGURE_EXTRA=()
if [[ "${CVC_PLATFORM}" == "openbsd" ]]; then
    CONFIGURE_EXTRA+=(am_cv_func_iconv=no)
fi

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --disable-dependency-tracking \
    --disable-nls \
    --without-ao \
    --without-speex \
    --without-flac \
    --without-curl \
    ${CONFIGURE_EXTRA[@]+"${CONFIGURE_EXTRA[@]}"}

make -j "${CVC_JOBS}"
make install-exec

cvc_rewrite_install_paths
