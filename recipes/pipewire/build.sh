#!/usr/bin/env bash
# recipes/pipewire/build.sh — build a lean PipeWire core on Linux with Meson.
#
# We build only libpipewire-0.3 + the SPA support/audio plugins.  Every
# optional backend and integration (ALSA, JACK, BlueZ, V4L2, systemd,
# GStreamer, PulseAudio, X11, dbus, udev, …) is disabled so the build is
# self-contained and carries no external system dependencies.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

cd "${CVC_SOURCE_DIR}"

# No pw-top, the only ncurses user.  meson.build looks up
# dependency('ncursesw', required : false), with no option to skip it, and
# src/tools builds pw-top whenever it is found: cvc.3's linux pw-top NEEDed
# libncursesw.so.6 from cvcpkg's ncurses in the shared populate prefix,
# undeclared (FreeBSD's pkg-config sees the same prefix).  A not-found
# dependency instead keeps the lean core free of ncurses and its terminfo
# database, which every libpipewire consumer would otherwise install.
sed -i.orig "s/dependency('ncursesw', required : false)/dependency('', required : false)/" meson.build
rm -f meson.build.orig
grep -q "ncurses_dep = dependency('', required : false)" meson.build

# FreeBSD: libpipewire calls dgettext/bindtextdomain (src/pipewire/pipewire.c,
# unconditionally), and FreeBSD's libc has no libintl, so meson.build looks for
# cc.find_library('intl'): the declared gettext's.  It searches only the link
# args, and -Dc_link_args below makes meson ignore LDFLAGS, so the deps
# prefix's lib/ goes there; its include/ goes first in CFLAGS, ahead of the
# /usr/local/include env-freebsd.sh adds.  The first FreeBSD build found a
# libintl.h but no library and failed with "unable to find library -lintl"
# (dev job 24666).  Linux has gettext in glibc.
_link_args="-Wl,-rpath,\$ORIGIN"
if [[ "${CVC_PLATFORM:-}" == freebsd ]]; then
    export CFLAGS="-I${CVC_DEPS_PREFIX}/include ${CFLAGS:-}"
    _link_args="-L${CVC_DEPS_PREFIX}/lib ${_link_args}"
fi

meson setup "${CVC_BUILD_DIR}" \
    --wrap-mode=nofallback \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig" \
    -Dc_link_args="${_link_args}" \
    -Dspa-plugins=enabled \
    -Dsession-managers="[]" \
    -Ddocs=disabled \
    -Dman=disabled \
    -Dexamples=disabled \
    -Dtests=disabled \
    -Dinstalled_tests=disabled \
    -Dgstreamer=disabled \
    -Dgstreamer-device-provider=disabled \
    -Dsystemd=disabled \
    -Dlogind=disabled \
    -Dselinux=disabled \
    -Dpipewire-alsa=disabled \
    -Dpipewire-jack=disabled \
    -Dpipewire-v4l2=disabled \
    -Dalsa=disabled \
    -Djack=disabled \
    -Dbluez5=disabled \
    -Dv4l2=disabled \
    -Dlibcamera=disabled \
    -Ddbus=disabled \
    -Dudev=disabled \
    -Dsdl2=disabled \
    -Dsndfile=disabled \
    -Dvulkan=disabled \
    -Dlibpulse=disabled \
    -Davahi=disabled \
    -Draop=disabled \
    -Dx11=disabled \
    -Dx11-xfixes=disabled \
    -Dlibcanberra=disabled \
    -Dreadline=disabled \
    -Dgsettings=disabled \
    -Dflatpak=disabled \
    -Dsnap=disabled \
    -Dlibusb=disabled \
    -Droc=disabled \
    -Dlv2=disabled \
    -Dopus=disabled \
    -Dlibmysofa=disabled \
    -Decho-cancel-webrtc=disabled \
    -Dffmpeg=disabled \
    -Dpw-cat=disabled \
    -Davb=disabled \
    -Dlibffado=disabled \
    -Dcompress-offload=disabled

ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

# Nothing installed may link ncurses (see the meson.build edit above).
_curses_users="$(grep -r -a -l -E 'lib(n)?cursesw?\.so' \
    "${CVC_INSTALL_DIR}/bin" "${CVC_INSTALL_DIR}/lib" 2>/dev/null || true)"
if [[ -n "${_curses_users}" ]]; then
    echo "pipewire build.sh: these link a curses library, which is not declared:" >&2
    echo "${_curses_users}" >&2
    exit 1
fi

# Make installed .pc files relocatable.
cvc_rewrite_install_paths
