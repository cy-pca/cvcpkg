#!/usr/bin/env bash
# recipes/glib/build.sh — build GLib on Linux and macOS with Meson.
#
# We build a lean GLib: no tests, no docs, no NLS, no introspection, no
# SELinux/libmount.  This is enough for GStreamer to link against
# glib/gobject/gio.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Our meson/ninja/pkg-config live in the prefix bin.
export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

# GLib's meson build hard-requires the python 'packaging' module
# (import('python').find_installation(modules: ['packaging'])).  If the
# builder's python3 lacks it, stage it into a throwaway dir on
# PYTHONPATH — this touches no system site-packages, so it is safe under
# PEP 668 (externally-managed environments).
if ! python3 -c 'import packaging' >/dev/null 2>&1; then
    _pkg_stage="$(mktemp -d)"
    python3 -m pip install --quiet --target "${_pkg_stage}" packaging \
        || pip3 install --quiet --target "${_pkg_stage}" packaging
    export PYTHONPATH="${_pkg_stage}${PYTHONPATH:+:${PYTHONPATH}}"
fi

# Embed $ORIGIN RPATH so glib's own libs (and consumers) resolve within
# whatever prefix they land in.
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    _rpath_flags="-Wl,-rpath,@loader_path"
else
    _rpath_flags="-Wl,-rpath,\$ORIGIN"
fi

_default_lib=shared
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    _default_lib=static
fi

cd "${CVC_SOURCE_DIR}"

# OpenBSD ships no iconv in libc, so GLib must link cvcpkg's GNU libiconv
# (staged by the iconv dep): put it on the compiler/linker search paths, where
# meson's iconv dependency finds it.  No-op where libc provides iconv and iconv
# is not a dep of this recipe.  (GLib 2.80 has no `iconv` option: the
# -Diconv=external this script used to pass failed meson with "Unknown
# options: iconv".)
#
# macOS has no gettext in libc either.  GLib always links a libintl, and when
# it finds none it builds its proxy-libintl subproject: a libintl.8.dylib with
# g_libintl_* symbols that the bundle never ships.  libglib then loaded whatever
# libintl.8.dylib the prefix held, and with gettext's real one there dyld
# aborted ("Symbol not found: _g_libintl_bind_textdomain_codeset", which broke
# qt6's rcc).  So macOS depends on cvcpkg's gettext, and meson must see it.
# c_link_args is set below, and meson then ignores LDFLAGS, so the -L goes there.
_link_flags="${_rpath_flags}"
if [ -n "${CVC_DEPS_PREFIX:-}" ] && { [ -f "${CVC_DEPS_PREFIX}/include/iconv.h" ] ||
                                      [ -f "${CVC_DEPS_PREFIX}/include/libintl.h" ]; }; then
    export CFLAGS="-I${CVC_DEPS_PREFIX}/include ${CFLAGS:-}"
    export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"
    export LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LIBRARY_PATH:+:${LIBRARY_PATH}}"
    _link_flags="-L${CVC_DEPS_PREFIX}/lib ${_rpath_flags}"
fi

# FreeBSD and OpenBSD have no getxattr(2) (FreeBSD's extended attributes are
# extattr_*), and GLib's xattr option defaults to on: on the OpenBSD builder
# meson stopped with "No getxattr implementation found in C library or
# libxattr".  GLib then simply has no xattr support in GFile.
#
# FreeBSD defines `environ` in the executable's startup code (crt1.o), not in
# libc.so, so libglib-2.0.so has to leave it undefined; meson's default
# b_lundef links shared libraries with --no-undefined and failed with
# "undefined symbol: environ" (gspawn.c, genviron.c).  FreeBSD's own
# devel/glib20 port builds with -Db_lundef=false for the same reason.
_meson_extra=()
case "${CVC_PLATFORM}" in
    freebsd) _meson_extra+=(-Dxattr=false -Db_lundef=false) ;;
    openbsd) _meson_extra+=(-Dxattr=false) ;;
esac

meson setup "${CVC_BUILD_DIR}" \
    --wrap-mode=nofallback \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --default-library="${_default_lib}" \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig" \
    -Dc_link_args="${_link_flags}" \
    -Dcpp_link_args="${_link_flags}" \
    -Dtests=false \
    -Dglib_debug=disabled \
    -Dnls=disabled \
    -Dman-pages=disabled \
    -Dselinux=disabled \
    -Dlibmount=disabled \
    -Dintrospection=disabled \
    -Ddtrace=false \
    -Dsysprof=disabled \
    ${_meson_extra[@]+"${_meson_extra[@]}"}

ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

# Make installed .pc/.cmake files relocatable.
cvc_rewrite_install_paths
