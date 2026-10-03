#!/usr/bin/env bash
# recipes/tvision/build.sh — build magiblot/tvision (Turbo Vision C++ TUI).
#
# tvision is designed to be consumed via add_subdirectory: it has NO install
# target and ships NO CMake package config. So this configures + builds the
# static libtvision, then stages the archive + public headers by hand and writes
# a pkg-config file (upstream provides none). tvision links libncursesw + libtinfo
# (from the ncurses recipe, which must be in the prefix); the .pc Requires pulls
# them onto the consumer's link line.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

# Prefer the ncurses RECIPE in the flat prefix over any system ncurses, so
# tvision's find_library(ncursesw)/find_library(tinfo) resolve hermetically.
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DCMAKE_PREFIX_PATH="${CVC_INSTALL_DIR}" \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DTV_BUILD_EXAMPLES=OFF \
    -DTV_BUILD_TESTS=OFF \
    -DTV_BUILD_USING_GPM=OFF
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"

# ---- stage (no install target upstream) ----
install -d "${CVC_INSTALL_DIR}/lib" "${CVC_INSTALL_DIR}/include" \
           "${CVC_INSTALL_DIR}/lib/pkgconfig"
# The build emits libtvision.a in the build root.
cp -a "${CVC_BUILD_DIR}"/libtvision.a "${CVC_INSTALL_DIR}/lib/"
# Public headers: include/tvision/ (consumers use <tvision/tv.h>).
cp -a "${CVC_SOURCE_DIR}/include/tvision" "${CVC_INSTALL_DIR}/include/"

# pkg-config — upstream ships none. Requires ncursesw so -ltinfo/-lncursesw land
# on the consumer's link line via `pkg-config --libs tvision`.
cat > "${CVC_INSTALL_DIR}/lib/pkgconfig/tvision.pc" <<'PC'
prefix=${pcfiledir}/../..
exec_prefix=${prefix}
libdir=${exec_prefix}/lib
includedir=${prefix}/include

Name: tvision
Description: Turbo Vision — a C++ TUI framework (magiblot port)
URL: https://github.com/magiblot/tvision
Version: 0.0.0-git.b4831e2
Requires.private: ncursesw
Libs: -L${libdir} -ltvision
Cflags: -I${includedir}
PC

if command -v cvc_rewrite_install_paths >/dev/null 2>&1; then
    cvc_rewrite_install_paths
fi
