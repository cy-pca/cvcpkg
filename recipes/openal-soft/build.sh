#!/usr/bin/env bash
# recipes/openal-soft/build.sh — build OpenAL Soft from source with CMake.
#
# OpenAL Soft dlopen's its audio backends (ALSA, PulseAudio, PipeWire,
# CoreAudio, WASAPI, sndio, ...) at runtime, so it carries no hard
# external link dependencies; whichever backend dev headers happen to be
# present on the build host get compiled in.  Honour CVC_LINK via the
# project's own LIBTYPE switch (STATIC / SHARED) — cvc_cmake_build also
# sets BUILD_SHARED_LIBS, but OpenAL Soft keys off LIBTYPE.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    _libtype=STATIC
else
    _libtype=SHARED
fi

cvc_cmake_build \
    -DLIBTYPE="${_libtype}" \
    -DALSOFT_EXAMPLES=OFF \
    -DALSOFT_UTILS=OFF \
    -DALSOFT_TESTS=OFF \
    -DALSOFT_INSTALL_EXAMPLES=OFF
