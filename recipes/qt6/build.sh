#!/usr/bin/env bash
# recipes/qt6/build.sh — build Qt 6 Base from source on Linux and macOS.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

# Linux: build the xcb QPA platform plugin against the hermetic X11/xcb recipes.
# Qt finds them via pkg-config; the protocol .pc files (xproto, …) live in
# share/pkgconfig. -DINPUT_xcb=yes makes a missing xcb a hard error (so we never
# silently ship a Qt with no platform plugin). macOS uses cocoa — skip all this.
CMAKE_EXTRA=()
if [[ "${CVC_PLATFORM}" == "linux" ]]; then
    export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
    CMAKE_EXTRA+=(-DINPUT_xcb=yes)
fi

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS="${BUILD_SHARED_LIBS}" \
    -DINPUT_opengl=yes \
    -DQT_BUILD_EXAMPLES=OFF \
    -DQT_BUILD_TESTS=OFF \
    -DQT_BUILD_BENCHMARKS=OFF \
    -DFEATURE_icu=OFF \
    -DFEATURE_sql_mysql=OFF \
    -DFEATURE_sql_psql=OFF \
    -DFEATURE_system_pcre2=ON \
    "${CMAKE_EXTRA[@]}"
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
