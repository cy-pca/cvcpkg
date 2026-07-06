#!/usr/bin/env bash
# recipes/idn2/build.sh — build GNU libidn2 via autotools.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-dependency-tracking
    --disable-doc
    --disable-gtk-doc
    --disable-nls
)
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

# Point autoconf at the cvcpkg-provided libunistring / libiconv when
# present so libidn2 links against our bundles rather than any system
# copy.  --with-libunistring-prefix is a Gnulib idiom that libidn2's
# configure recognises.
if [[ -d "${CVC_DEPS_PREFIX:-}/include" ]]; then
    CONFIGURE_ARGS+=(--with-libunistring-prefix="${CVC_DEPS_PREFIX}")
    case "$(uname -s)" in
        Darwin|FreeBSD|NetBSD|OpenBSD)
            CONFIGURE_ARGS+=(--with-libiconv-prefix="${CVC_DEPS_PREFIX}")
            ;;
    esac
fi

# Ensure pkg-config and the linker find bundle-supplied deps.
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX:-}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CPPFLAGS="${CPPFLAGS:-} -I${CVC_DEPS_PREFIX:-/nonexistent}/include"
export LDFLAGS="${LDFLAGS:-} -L${CVC_DEPS_PREFIX:-/nonexistent}/lib"

MAKE=make
command -v gmake >/dev/null 2>&1 && MAKE=gmake

./configure "${CONFIGURE_ARGS[@]}"
$MAKE -j "${CVC_JOBS}"
$MAKE install

if command -v cvc_rewrite_install_paths >/dev/null 2>&1; then
    cvc_rewrite_install_paths
fi
