#!/usr/bin/env bash
# recipes/portaudio/build.sh — build PortAudio with CMake (Linux/macOS).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Expose the cvcpkg dependency closure so PortAudio's FIND_PACKAGE(ALSA)
# (Linux) locates our hermetic ALSA (alsa-lib) build.  macOS links the
# CoreAudio system frameworks and needs nothing from the closure.
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}${CMAKE_PREFIX_PATH:+:${CMAKE_PREFIX_PATH}}"

# PortAudio has its OWN static/shared switch (PA_BUILD_STATIC /
# PA_BUILD_SHARED), both ON by default; it ignores BUILD_SHARED_LIBS.
# Translate CVC_LINK so each variant builds exactly one library kind.
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    _pa_static=ON
    _pa_shared=OFF
else
    _pa_static=OFF
    _pa_shared=ON
fi

_pa_opts=(
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}"
    -DPA_BUILD_STATIC="${_pa_static}"
    -DPA_BUILD_SHARED="${_pa_shared}"
    -DPA_LIBNAME_ADD_SUFFIX=OFF
    -DPA_BUILD_TESTS=OFF
    -DPA_BUILD_EXAMPLES=OFF
)

case "${CVC_PLATFORM}" in
    linux)
        # ALSA backend (alsa-lib dep).  Keep JACK OFF to avoid a hard
        # jack runtime dependency and keep the closure small.
        _pa_opts+=( -DPA_USE_ALSA=ON -DPA_USE_JACK=OFF )
        ;;
    macos)
        # CoreAudio is enabled automatically on Apple (PA_USE_COREAUDIO=ON).
        :
        ;;
    freebsd)
        # OSS is FreeBSD's native audio interface (ALSA is Linux-only).
        _pa_opts+=( -DPA_USE_OSS=ON -DPA_USE_ALSA=OFF -DPA_USE_JACK=OFF )
        ;;
esac

cvc_cmake_build "${_pa_opts[@]}"
