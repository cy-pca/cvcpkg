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

# OpenBSD has a sys/audioio.h, so CMake enables the Solaris backend, which
# then fails on Solaris-only constants (AUDIO_ENCODING_LINEAR, AUDIO_SETINFO);
# OpenBSD's audio goes through the sndio backend.
_extra=()
if [ "$(uname -s)" = OpenBSD ]; then _extra+=(-DALSOFT_BACKEND_SOLARIS=OFF); fi
# OpenBSD: no libatomic.  CMakeLists.txt links -latomic whenever a test program
# links with it (HAVE_LIBATOMIC), not only when atomics need it.  Base has no
# libatomic, but env-openbsd.sh's -L/usr/local/lib reaches the ports gcc's, so
# cvc.2's libopenal.so.1.24.3 NEEDed the builder's libatomic.so.4.0.  Base
# clang inlines the lock-free atomics OpenAL Soft uses: preset the result to
# skip the check, and the check after the build proves nothing is left.
# (FreeBSD's ports gcc keeps libatomic under /usr/local/lib/gcc<N>, which no -L
# names.)
if [[ "${CVC_PLATFORM}" == "openbsd" ]]; then _extra+=(-DHAVE_LIBATOMIC=OFF); fi
cvc_cmake_build \
    ${_extra[@]+"${_extra[@]}"} \
    -DLIBTYPE="${_libtype}" \
    -DALSOFT_EXAMPLES=OFF \
    -DALSOFT_UTILS=OFF \
    -DALSOFT_TESTS=OFF \
    -DALSOFT_INSTALL_EXAMPLES=OFF

# Without libatomic, an atomic clang could not inline would be an undefined
# __atomic_* call that fails only when the library loads.
if [[ "${CVC_PLATFORM}" == "openbsd" && "${_libtype}" == "SHARED" ]]; then
    for _lib in "${CVC_INSTALL_DIR}"/lib/libopenal.so.*; do
        [[ -f "${_lib}" && ! -L "${_lib}" ]] || continue
        _undef="$(nm -D -u "${_lib}" 2>/dev/null || true)"
        if [[ -z "${_undef}" ]]; then
            echo "openal-soft: nm could not read ${_lib}" >&2
            exit 1
        fi
        if grep -E '__atomic_' <<<"${_undef}"; then
            echo "openal-soft: ${_lib##*/} needs libatomic for the atomics above" >&2
            exit 1
        fi
        echo "openal-soft: ${_lib##*/} needs no libatomic"
    done
fi
