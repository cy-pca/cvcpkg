#!/usr/bin/env bash
# recipes/libtool/build.sh — build GNU Libtool from source.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# Touch autotools timestamps so make doesn't try to regenerate them
# with tools (aclocal-1.17, etc.) that aren't installed.
find . \( -name 'aclocal.m4' -o -name 'configure' -o -name 'Makefile.in' \
       -o -name 'config.h.in' -o -name '*.info' \) \
    -print0 2>/dev/null | xargs -0 -r touch

./configure \
    --prefix="${CVC_INSTALL_DIR}"

# Prefer gmake on the BSDs.
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        if command -v gmake >/dev/null 2>&1; then MAKE=gmake; fi
        ;;
esac

# MAKEINFO=true: the shipped doc/libtool.info is used as is.  Built from the
# libtool-src package, the tree comes with one mtime throughout, so the touch
# above leaves configure newer than doc/stamp-vti; its rule then rewrites
# doc/version.texi with libtool.texi's (new) date, and make would rebuild the
# manual with a makeinfo most hosts lack (Windows: "makeinfo: command not
# found", Error 127).
"${MAKE}" -j "${CVC_JOBS}" AUTOMAKE=true ACLOCAL=true AUTOCONF=true AUTOHEADER=true MAKEINFO=true
"${MAKE}" install AUTOMAKE=true ACLOCAL=true AUTOCONF=true AUTOHEADER=true MAKEINFO=true
