#!/usr/bin/env bash
# recipes/gettext/build.sh — build GNU gettext from source using autotools.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# macOS builds the full tree too.  It used to build only gettext-runtime
# (libintl): the full build's libtextstyle failed to link on arm64
# ("undefined symbol _iconv_ostream_create") because gnulib's "working iconv"
# test fails against macOS's non-GNU system iconv, so its iconv-ostream module
# was never compiled.  The message tools (msgfmt, xgettext, msgmerge) were then
# left to the build host -- Homebrew -- and with Homebrew gone libpq and
# postgresql (-Dnls=enabled) failed on macOS: "Program 'msgfmt' not found".
# macOS now builds against cvcpkg's GNU libiconv (the iconv dependency, as on
# OpenBSD; picked up below), which passes that test.

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

# OpenBSD has no iconv in libc, and macOS has a non-GNU one: use cvcpkg's GNU
# libiconv (staged into CVC_DEPS_PREFIX by the openbsd/macos iconv dep) so the
# bundled libxml finds <iconv.h> and links libiconv, and gnulib's iconv test
# passes.  A no-op where iconv isn't a dep (other platforms use libc's iconv).
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
