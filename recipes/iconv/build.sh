#!/usr/bin/env bash
# recipes/iconv/build.sh — build GNU libiconv from source via autotools.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-dependency-tracking
)
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi
# macOS: no NLS.  libiconv's configure links an external libintl when it finds
# one, and the x86_64 cvc.3 bin/iconv loaded Homebrew's
# /usr/local/opt/gettext/lib/libintl.8.dylib.  (glibc carries gettext itself, so
# Linux keeps its message catalogs.)
if [[ "${CVC_PLATFORM:-}" == "macos" ]]; then
    CONFIGURE_ARGS+=(--disable-nls)
fi

MAKE=make
command -v gmake >/dev/null 2>&1 && MAKE=gmake

./configure "${CONFIGURE_ARGS[@]}"
$MAKE -j "${CVC_JOBS}"
$MAKE install

# Strip libtool archives (.la), as recipes/libx11 does: they record this job's
# scratch prefix (libdir; OpenBSD libraries have no DT_SONAME), which is gone once
# cvcpkg relocates the bundle, and a downstream libtool link follows them:
# on OpenBSD, gettext linked libiconv.so.8.1 through it by full path, and with no
# soname that scratch path became the binaries' NEEDED entry, which the pack
# gate refuses.
# The shared library and the pkg-config .pc carry the real link information.
find "${CVC_INSTALL_DIR}" -name '*.la' -delete

if command -v cvc_rewrite_install_paths >/dev/null 2>&1; then
    cvc_rewrite_install_paths
fi
