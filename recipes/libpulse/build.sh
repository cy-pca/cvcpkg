#!/usr/bin/env bash
# recipes/libpulse/build.sh — build the PulseAudio client library on Linux.
#
# PulseAudio is built with -Ddaemon=false so only the client libraries
# (libpulse, libpulse-simple) are produced — no server, no ALSA/BlueZ/…
# modules.  Its one hard dependency is libsndfile (built as a recipe);
# every optional integration is disabled to keep the bundle lean and
# self-contained.
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
    -Ddaemon=false \
    -Dclient=true \
    -Dbashcompletiondir="${CVC_INSTALL_DIR}/share/bash-completion/completions" \
    -Dzshcompletiondir="${CVC_INSTALL_DIR}/share/zsh/site-functions" \
    -Ddoxygen=false \
    -Dman=false \
    -Dtests=false \
    -Ddatabase=simple \
    -Dalsa=disabled \
    -Dasyncns=disabled \
    -Davahi=disabled \
    -Dbluez5=disabled \
    -Dconsolekit=disabled \
    -Ddbus=disabled \
    -Delogind=disabled \
    -Dfftw=disabled \
    -Dglib=disabled \
    -Dgsettings=disabled \
    -Dgstreamer=disabled \
    -Dgtk=disabled \
    -Dhal-compat=false \
    -Djack=disabled \
    -Dlirc=disabled \
    -Dopenssl=disabled \
    -Dorc=disabled \
    -Doss-output=disabled \
    -Dsamplerate=disabled \
    -Dsoxr=disabled \
    -Dspeex=disabled \
    -Dsystemd=disabled \
    -Dtcpwrap=disabled \
    -Dudev=disabled \
    -Dvalgrind=disabled \
    -Dx11=disabled \
    -Dwebrtc-aec=disabled

ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

# Make installed .pc/.cmake files relocatable.
cvc_rewrite_install_paths
