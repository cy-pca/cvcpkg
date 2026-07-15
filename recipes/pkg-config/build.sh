#!/usr/bin/env bash
# recipes/pkg-config/build.sh — build pkg-config from source.
#
# Uses --with-internal-glib so no external glib dependency is needed.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# OpenBSD ships no iconv in libc, so the bundled (internal) glib's configure
# fails "No iconv() implementation found".  Link cvcpkg's GNU libiconv there
# (openbsd-only dep).  No-op where libc provides iconv.
CONFIGURE_ICONV=()
if [ -n "${CVC_DEPS_PREFIX:-}" ] && [ -f "${CVC_DEPS_PREFIX}/include/iconv.h" ]; then
    export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
    export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"
    export LIBS="-liconv ${LIBS:-}"
    CONFIGURE_ICONV=(--with-libiconv-prefix="${CVC_DEPS_PREFIX}")
fi

./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --with-internal-glib \
    --disable-dependency-tracking \
    --disable-nls \
    "${CONFIGURE_ICONV[@]}"

make -j "${CVC_JOBS}"
make install
