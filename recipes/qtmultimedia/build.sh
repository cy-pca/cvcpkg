#!/usr/bin/env bash
# recipes/qtmultimedia/build.sh — build Qt Multimedia module on Linux and macOS.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Let Qt's configure discover the multimedia backends we built as
# dependencies: FFmpeg (via CMake find modules on CMAKE_PREFIX_PATH) and
# GStreamer / PipeWire (via pkg-config).
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

cd "${CVC_SOURCE_DIR}"

# Qt module plugins (ffmpeg, gstreamer) call qt_find_package(EGL), which
# Qt resolves through its "PlatformGraphics" package.  Its find module
# lives in <qt>/lib/cmake/Qt6/platforms/ — a directory that Qt only adds
# to CMAKE_MODULE_PATH when you build via its toolchain file / qt-cmake.
# In this plain find_package(Qt6) build it isn't on the path, so
# find_package(PlatformGraphics) errors and both backend plugins get
# skipped (leaving qtmultimedia without any media backend).  Seed the
# platforms dir so the (soft) EGL lookup resolves and the plugins build.
_qt_platforms_dir="${CVC_DEPS_PREFIX}/lib/cmake/Qt6/platforms"

# Backend selection: Qt 6.8 gates its FFmpeg media backend on a low-level
# audio backend — on Linux that means PulseAudio (QT_FEATURE_pulseaudio),
# which we provide via the libpulse recipe.  However, Qt's FFmpeg plugin
# also pulls in an eglfs screen-capture path that hard-requires Qt::Quick
# (qtdeclarative), which we do not ship — so on Linux we build the
# GStreamer media backend instead (it handles its own media/audio) while
# libpulse still gives Qt a working low-level PulseAudio audio backend.
# On macOS the FFmpeg backend has no such coupling, so leave it to Qt's
# auto-detection there.
_backend_flags=()
if [[ "${CVC_PLATFORM}" == "linux" ]]; then
    _backend_flags+=(-DFEATURE_ffmpeg=OFF)
fi

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS="${BUILD_SHARED_LIBS}" \
    -DCMAKE_MODULE_PATH="${_qt_platforms_dir}" \
    -DQT_BUILD_EXAMPLES=OFF \
    -DQT_BUILD_TESTS=OFF \
    -DQT_BUILD_BENCHMARKS=OFF \
    "${_backend_flags[@]}"
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
