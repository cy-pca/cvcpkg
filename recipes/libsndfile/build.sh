#!/usr/bin/env bash
# recipes/libsndfile/build.sh — build libsndfile on Linux/macOS/BSD via CMake.
#
# We build a lean libsndfile: the built-in formats (WAV/AIFF/AU/…) with
# no external codec libraries (FLAC/Ogg/Vorbis/Opus/MPEG).  This is all
# PulseAudio's client library needs, and keeps the bundle dependency-free.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cvc_cmake_build \
    -DBUILD_TESTING=OFF \
    -DBUILD_PROGRAMS=OFF \
    -DBUILD_EXAMPLES=OFF \
    -DENABLE_EXTERNAL_LIBS=OFF \
    -DENABLE_MPEG=OFF \
    -DENABLE_CPACK=OFF \
    -DINSTALL_MANPAGES=OFF
