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
# (staged by the openbsd-only iconv dep).  Force meson's external-iconv path
# and put our libiconv on the compiler/linker search paths.  No-op elsewhere
# (libc provides iconv), where iconv is not a dep of this recipe.
ICONV_ARGS=()
if [ -n "${CVC_DEPS_PREFIX:-}" ] && [ -f "${CVC_DEPS_PREFIX}/include/iconv.h" ]; then
    ICONV_ARGS=(-Diconv=external)
    export CFLAGS="-I${CVC_DEPS_PREFIX}/include ${CFLAGS:-}"
    export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib ${LDFLAGS:-}"
    export LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LIBRARY_PATH:+:${LIBRARY_PATH}}"
fi

meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --default-library="${_default_lib}" \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig" \
    "${ICONV_ARGS[@]}" \
    -Dc_link_args="${_rpath_flags}" \
    -Dcpp_link_args="${_rpath_flags}" \
    -Dtests=false \
    -Dglib_debug=disabled \
    -Dnls=disabled \
    -Dman-pages=disabled \
    -Dselinux=disabled \
    -Dlibmount=disabled \
    -Dintrospection=disabled \
    -Ddtrace=false \
    -Dsysprof=disabled

ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

# Make installed .pc/.cmake files relocatable.
cvc_rewrite_install_paths
