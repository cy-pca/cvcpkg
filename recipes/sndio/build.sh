#!/usr/bin/env bash
# recipes/sndio/build.sh — build libsndio, the portable sndio client library.
#
# sndio ships a hand-written (NOT autotools) ./configure. It has no external
# dependencies, so there is nothing to expose from the deps prefix.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"

# -fPIC so the static archive can also be linked into shared consumers; -O2 for
# optimisation.  We deliberately do NOT push a -Wl,-rpath,$ORIGIN through
# LDFLAGS: sndio's Makefile links the shared lib with an unquoted
# `${CC} ${LDFLAGS}`, so a literal $ORIGIN gets eaten by make/shell expansion
# ($O -> empty make var, then $ORIGIN -> empty shell var).  libsndio needs no
# rpath of its own anyway — its only NEEDED entry is libc.
export CFLAGS="-O2 -fPIC${CFLAGS:+ ${CFLAGS}}"

# CVC_LINK -> sndio's own static/dynamic switch.  These are mutually exclusive
# in sndio's configure (each flag disables the other): --enable-dynamic builds
# only libsndio.so*, --enable-static builds only libsndio.a.  package.files
# globs lib/libsndio* so it captures whichever variant is produced.
_link=(--enable-dynamic)
[[ "${CVC_LINK:-shared}" == "static" ]] && _link=(--enable-static)

# On Linux the default ALSA backend links -lasound, a system dependency we do
# not ship (there is no cvcpkg alsa recipe).  Disable it to keep libsndio
# self-contained; the client library still reaches audio via a running sndiod
# over its socket (aucat) backend.  BSD default backends (sun/oss) are provided
# by the kernel and need no extra link libraries.
_extra=()
[[ "${CVC_PLATFORM}" == "linux" ]] && _extra+=(--disable-alsa)

cd "${CVC_SOURCE_DIR}"
./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --pkgconfdir="${CVC_INSTALL_DIR}/lib/pkgconfig" \
    "${_link[@]}" "${_extra[@]}"

# Build/install ONLY the client library (libsndio) — not the sndiod daemon or
# the aucat/midicat/sndioctl tools.  Those are not part of this package and on
# Linux the daemon would require the ALSA backend disabled above.  libsndio's
# install target touches only include/, lib/ and share/man (no /var runtime
# dirs), so no DESTDIR juggling is needed.
make -C libsndio -j "${CVC_JOBS}"
make -C libsndio install

cvc_rewrite_install_paths
