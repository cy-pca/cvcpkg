#!/usr/bin/env bash
# recipes/lame/build.sh — build the lame MP3 encoder CLI tool.
#
# Built from the same source tarball as libmp3lame, against the in-tree STATIC
# libmp3lame, so the binary is self-contained.  Only bin/lame is installed;
# the shared library is the libmp3lame recipe's.
#
# This used to configure with both --disable-shared and --disable-static and
# run `make -C frontend` alone, expecting the packaged libmp3lame from the
# deps prefix.  But the frontend's Makefile links ../libmp3lame/libmp3lame.la
# from this tree, and nothing had built it ("No rule to make target
# '../libmp3lame/libmp3lame.la', needed by 'lame'"), so lame failed on every
# platform.  Build the whole tree with the static library instead.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"

# OpenBSD: link cvcpkg's GNU libiconv, the declared iconv dependency.  Its libc
# has no iconv, so cvc.4's bin/lame took the build host's ports libiconv.so.7.1
# from /usr/local/lib, which env-openbsd.sh puts on LDFLAGS.  The frontend uses
# iconv to write ID3 tag text from the locale's charset as Latin-1 or UTF-16.
# The deps prefix goes first so it wins over /usr/local.
CONFIGURE_EXTRA=()
if [[ "${CVC_PLATFORM}" == "openbsd" ]]; then
    if [[ ! -f "${CVC_DEPS_PREFIX}/include/iconv.h" ]]; then
        echo "cvcpkg: lame: the declared iconv is missing from ${CVC_DEPS_PREFIX}" >&2
        exit 1
    fi
    export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
    export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"
    CONFIGURE_EXTRA+=(--with-libiconv-prefix="${CVC_DEPS_PREFIX}")
fi

cd "${CVC_SOURCE_DIR}"

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --disable-dependency-tracking \
    --enable-frontend \
    --enable-static \
    --disable-shared \
    --disable-nls \
    ${CONFIGURE_EXTRA[@]+"${CONFIGURE_EXTRA[@]}"}

make -j "${CVC_JOBS}"

mkdir -p "${CVC_INSTALL_DIR}/bin"
install -m 755 frontend/lame "${CVC_INSTALL_DIR}/bin/lame"

# On OpenBSD the libiconv bin/lame links must be the declared one (see above).
if [[ "${CVC_PLATFORM}" == "openbsd" ]]; then
    for _lib in $(grep -a -h -o -E 'libiconv\.so\.[0-9.]+' "${CVC_INSTALL_DIR}/bin/lame" | sort -u || true); do
        if [[ -e "${CVC_DEPS_PREFIX}/lib/${_lib}" ]]; then
            echo "cvcpkg: bin/lame links ${_lib} from the declared iconv"
            continue
        fi
        echo "cvcpkg: bin/lame linked ${_lib}, which is not the declared iconv" >&2
        exit 1
    done
fi
