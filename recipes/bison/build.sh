#!/usr/bin/env bash
# recipes/bison/build.sh — build GNU Bison from source on Linux, macOS, BSD.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Bison is an autotools project, not CMake — source the common env
# only for the variables, not for cvc_cmake_build.
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# BSD make can't parse Bison's Makefile; require GNU make there.
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        if command -v gmake >/dev/null 2>&1; then
            MAKE=gmake
        fi
        ;;
esac

cd "${CVC_SOURCE_DIR}"

# OpenBSD: no iconv.  Its libc has none, so configure linked the build host's
# ports libiconv.so.7.1, found through env-openbsd.sh's -L/usr/local/lib, and
# cvc.5's bin/bison NEEDed it undeclared.  Bison only uses iconv to print its
# counterexample arrows and bullets in a non-UTF-8 locale (lib/unicodeio.c).
# OpenBSD's only locales are C and UTF-8, and both print the same without it.
CONFIGURE_EXTRA=()
if [[ "${CVC_PLATFORM}" == "openbsd" ]]; then
    CONFIGURE_EXTRA+=(am_cv_func_iconv=no)
fi

# --enable-relocatable: find the m4 skeletons (share/bison) relative to the
# installed binary instead of this build's temporary prefix, which does not
# exist where the bundle is unpacked ("m4sugar.m4: cannot open").
# M4=m4: run whatever m4 is on PATH, not the absolute build-time path of the
# m4 host tool ("m4 subprocess failed").  The m4 runtime dep puts one there.
./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --enable-relocatable \
    --disable-nls \
    M4=m4 \
    ${CONFIGURE_EXTRA[@]+"${CONFIGURE_EXTRA[@]}"}

"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install
