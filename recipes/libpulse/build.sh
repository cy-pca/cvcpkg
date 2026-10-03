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

# RPATH must span both install dirs: libpulse.so / libpulse-simple.so land in
# lib/ but NEED libpulsecommon-*.so, which PulseAudio installs in lib/pulseaudio/;
# and libpulsecommon in turn NEEDs libsndfile from lib/.  So every built object
# gets $ORIGIN (lib siblings), $ORIGIN/pulseaudio (down into the privlib dir),
# and $ORIGIN/.. (up to lib/ from within pulseaudio/).  With only $ORIGIN the
# libraries can't resolve each other at link OR run time — a consumer linking
# -lpulse fails with "undefined reference to pa_*" (libpulsecommon symbols).

meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig" \
    -Dc_link_args="-Wl,-rpath,\$ORIGIN -Wl,-rpath,\$ORIGIN/pulseaudio -Wl,-rpath,\$ORIGIN/.." \
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

# meson stamps the ABSOLUTE privlib dir (<prefix>/lib/pulseaudio) into the
# RUNPATH of libpulse/libpulse-simple as an auto-detected dependency path — a
# build-temp leak that dies once the bundle is relocated.  Rewrite each library
# to a clean $ORIGIN-relative RUNPATH so the bundle resolves from any prefix:
#   lib/libpulse*.so       -> $ORIGIN (siblings) + $ORIGIN/pulseaudio (libpulsecommon)
#   lib/pulseaudio/*.so    -> $ORIGIN (peers)    + $ORIGIN/.. (libsndfile in lib/)
# Best-effort: if patchelf is unavailable the -Dc_link_args $ORIGIN entries above
# still cover resolution (the absolute entry is simply dead after relocation).
if command -v patchelf >/dev/null 2>&1; then
    shopt -s nullglob
    for _so in "${CVC_INSTALL_DIR}"/lib/libpulse*.so*; do
        [[ -L "${_so}" ]] && continue
        patchelf --set-rpath '$ORIGIN:$ORIGIN/pulseaudio' "${_so}" || true
    done
    for _so in "${CVC_INSTALL_DIR}"/lib/pulseaudio/*.so*; do
        [[ -L "${_so}" ]] && continue
        patchelf --set-rpath '$ORIGIN:$ORIGIN/..' "${_so}" || true
    done
    shopt -u nullglob
fi
