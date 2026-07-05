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

meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig" \
    -Dc_link_args="-Wl,-rpath,\$ORIGIN" \
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

# Make installed .pc files relocatable.
cvc_rewrite_install_paths
