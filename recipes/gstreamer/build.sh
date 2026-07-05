#!/usr/bin/env bash
# recipes/gstreamer/build.sh — build GStreamer (core + base + good + bad)
# on Linux and macOS from the upstream mono-repository with Meson.
#
# We enable the base/good/bad subprojects and disable ugly/libav,
# bindings, dev tools, docs and tests.  gst-plugins-bad is enabled
# primarily for its gstreamer-play-1.0 library (libgstplay), which Qt
# Multimedia's GStreamer backend requires; --wrap-mode=nofallback keeps
# it lean by disabling any bad plugin whose external deps are absent.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

# cvcpkg's bison bakes its build-time prefix into the binary as the
# location of its m4 skeletons (share/bison/m4sugar/…).  Once relocated
# into the shared deps prefix that path no longer exists, so bison fails
# with "m4sugar.m4: cannot open".  Point it at the real skeleton dir.
if [[ -d "${CVC_DEPS_PREFIX}/share/bison" ]]; then
    export BISON_PKGDATADIR="${CVC_DEPS_PREFIX}/share/bison"
fi

if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    _rpath_flags="-Wl,-rpath,@loader_path"
else
    _rpath_flags="-Wl,-rpath,\$ORIGIN"
fi

cd "${CVC_SOURCE_DIR}"

meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig" \
    --wrap-mode=nofallback \
    -Dc_link_args="${_rpath_flags}" \
    -Dcpp_link_args="${_rpath_flags}" \
    -Dbase=enabled \
    -Dgood=enabled \
    -Dugly=disabled \
    -Dbad=enabled \
    -Dlibav=disabled \
    -Ddevtools=disabled \
    -Dges=disabled \
    -Drtsp_server=disabled \
    -Drs=disabled \
    -Dvaapi=disabled \
    -Dgst-examples=disabled \
    -Dpython=disabled \
    -Dsharp=disabled \
    -Dtls=disabled \
    -Dlibnice=disabled \
    -Dqt5=disabled \
    -Dqt6=disabled \
    -Dwebrtc=disabled \
    -Dintrospection=disabled \
    -Dnls=disabled \
    -Dorc=disabled \
    -Ddoc=disabled \
    -Dgtk_doc=disabled \
    -Dtests=disabled \
    -Dexamples=disabled \
    -Dtools=enabled \
    -Dgpl=disabled

ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

# Make installed .pc files relocatable.
cvc_rewrite_install_paths
