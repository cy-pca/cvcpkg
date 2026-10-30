#!/usr/bin/env bash
# recipes/qt6/build.sh — build Qt 6 Base from source on Linux and macOS.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

# Linux, FreeBSD and NetBSD: build the xcb QPA platform plugin against the
# hermetic X11/xcb recipes, and OpenGL against cvcpkg's libglvnd (mesa supplies
# the driver on the two BSDs). Qt finds them via pkg-config; the protocol .pc
# files (xproto, …) live in share/pkgconfig. -DINPUT_xcb=yes makes a missing xcb
# a hard error (so we never silently ship a Qt with no platform plugin). macOS
# uses cocoa and OpenBSD its base X11 — skip all this.
CMAKE_EXTRA=()
if [[ "${CVC_PLATFORM}" == "linux" || "${CVC_PLATFORM}" == "freebsd" || "${CVC_PLATFORM}" == "netbsd" ]]; then
    export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
    CMAKE_EXTRA+=(-DINPUT_xcb=yes)
fi
# NetBSD: keep the base system's X11 sets (/usr/X11R7, Mesa included) out of the
# build.  CMake's FindX11 searches /usr/X11R7 by name, so it found libSM and
# libICE there (the prefix has neither) and the xcb plugin's session management
# linked them (netbsd-build-2, 2026-10-30), which ties it to hosts with the X sets.
# Also preset Qt's dladdr test: it links -ldl, and NetBSD has no libdl (dladdr is
# in libc), so it failed and qlogging.cpp took its non-dladdr backtrace path,
# which clang rejects ("1st argument ('void *const') would lose const
# qualifier", the error OpenBSD hit, see below).  This keeps the backtraces.
if [[ "${CVC_PLATFORM}" == "netbsd" ]]; then
    CMAKE_EXTRA+=("-DCMAKE_IGNORE_PATH=/usr/X11R7/include;/usr/X11R7/lib" -DTEST_dladdr=ON)
fi
# OpenBSD: no backtrace feature.  Qt 6.8.2's qlogging.cpp passes a const frame
# pointer to a lambda taking `void *&` in its execinfo backtrace path, which
# clang rejects there ("1st argument ('void *const') would lose const
# qualifier", populate 2026-10-19).  It only drops stack traces from fatal
# messages.
if [[ "${CVC_PLATFORM}" == "openbsd" ]]; then
    CMAKE_EXTRA+=(-DFEATURE_backtrace=OFF)
    # qelfparser_p.cpp names ELFOSABI_AIX, which OpenBSD's <elf.h> spells
    # ELFOSABI_MONTEREY (AIX's old name, value 7): "use of undeclared
    # identifier 'ELFOSABI_AIX'" (job 22627).  It has every other ELFOSABI_*
    # the parser uses.
    export CXXFLAGS="${CXXFLAGS:-} -DELFOSABI_AIX=7"
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
    ${CMAKE_EXTRA[@]+"${CMAKE_EXTRA[@]}"}
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
