#!/usr/bin/env bash
# recipes/gettext/build.sh — build GNU gettext from source using autotools.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# On macOS, build only the gettext-runtime sub-package (libintl).  The full
# gettext build also compiles libtextstyle, whose iconv-ostream module
# references iconv_ostream_create but never gets compiled because gnulib's
# "working iconv" test fails against macOS's (non-GNU) system iconv -> an
# arm64 "undefined symbol _iconv_ostream_create" link error.  Downstream
# consumers only need libintl, which gettext-runtime provides; the message
# tools (msgfmt/xgettext) come from the host toolchain (Homebrew) at build
# time.  linux/BSD build the full tree as before.
if [[ "$(uname -s)" == "Darwin" ]]; then
    cd gettext-runtime
fi

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-java
    --disable-csharp
    --without-emacs
    --without-git
    --without-bzip2
    --without-xz
)

# Respect static/shared link mode.
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

# OpenBSD has no iconv in libc; use cvcpkg's GNU libiconv (staged into
# CVC_DEPS_PREFIX by the openbsd-only iconv dep) so the bundled libxml finds
# <iconv.h> and links libiconv.  A no-op where iconv isn't a dep (other
# platforms use their libc iconv).
if [[ -n "${CVC_DEPS_PREFIX:-}" && -f "${CVC_DEPS_PREFIX}/include/iconv.h" ]]; then
    export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include ${CPPFLAGS:-}"
    export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"
    CONFIGURE_ARGS+=(--with-libiconv-prefix="${CVC_DEPS_PREFIX}")
fi

# On BSDs, use gmake if available.
if command -v gmake >/dev/null 2>&1; then
    MAKE=gmake
else
    MAKE=make
fi
export MAKE

./configure "${CONFIGURE_ARGS[@]}"
$MAKE -j "${CVC_JOBS}"
$MAKE install
