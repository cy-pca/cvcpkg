#!/usr/bin/env bash
# recipes/log4cplus/build.sh — build log4cplus from source.
#
# Two passes: upstream log4cplus builds ONE flavor per configure
# (BUILD_SHARED_LIBS toggles the real target between `log4cplus` [shared]
# and `log4cplusS` [static]), and BOTH passes write the same
# lib/cmake/log4cplus/log4cplusTargets.cmake — last install wins.
#
# The bundle ships both libs, so order the passes to match the bundle's
# link variant: the PRIMARY flavor installs LAST and owns the CMake
# package export. A release/shared bundle therefore exports
# `log4cplus::log4cplus` (what upstream/apt/vcpkg consumers link), with
# liblog4cplusS.a still on disk for manual static linking. Previously the
# static pass always ran last, so shared bundles exported ONLY
# `log4cplus::log4cplusS` and find_package consumers linking
# `log4cplus::log4cplus` failed at generate time.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

COMMON_ARGS=(
    -DLOG4CPLUS_BUILD_TESTING=OFF
    -DLOG4CPLUS_BUILD_LOGGINGSERVER=OFF
    -DWITH_UNIT_TESTS=OFF
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON
)

build_flavor() { # $1 = subdir, $2 = BUILD_SHARED_LIBS value
    cmake -G Ninja \
        -S "${CVC_SOURCE_DIR}" \
        -B "${CVC_BUILD_DIR}/$1" \
        -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
        -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
        -DBUILD_SHARED_LIBS="$2" \
        "${COMMON_ARGS[@]}"
    cmake --build "${CVC_BUILD_DIR}/$1" -j "${CVC_JOBS}"
    cmake --install "${CVC_BUILD_DIR}/$1"
}

if [ "${CVC_LINK:-shared}" = "static" ]; then
    build_flavor shared ON    # secondary: extra lib only
    build_flavor static OFF   # primary: owns the CMake export
else
    build_flavor static OFF   # secondary: extra lib only
    build_flavor shared ON    # primary: owns the CMake export
fi

CMAKE_PKG_DIR="${CVC_INSTALL_DIR}/lib/cmake/log4cplus"

# Upstream bakes find_library() absolute paths for system libs (e.g.
# /usr/lib/x86_64-linux-gnu/librt.a, libnsl.so on glibc) into
# INTERFACE_LINK_LIBRARIES; rewrite them to plain -l names so the bundle
# links on distros with different system-library layouts.
sed -E -i.cvcbak 's#/usr/lib[^;"]*/lib([A-Za-z0-9_+-]+)\.(a|so[.0-9]*)#\1#g' \
    "${CMAKE_PKG_DIR}/log4cplusTargets.cmake"
rm -f "${CMAKE_PKG_DIR}/log4cplusTargets.cmake.cvcbak"

# Uniform consumer target: a static-primary export defines only
# log4cplus::log4cplusS. Wrap it so find_package consumers can link
# log4cplus::log4cplus regardless of the bundle's link variant.
cat >> "${CMAKE_PKG_DIR}/log4cplusConfig.cmake" <<'EOF'

if(NOT TARGET log4cplus::log4cplus AND TARGET log4cplus::log4cplusS)
  add_library(log4cplus::log4cplus INTERFACE IMPORTED)
  set_target_properties(log4cplus::log4cplus PROPERTIES
    INTERFACE_LINK_LIBRARIES log4cplus::log4cplusS)
endif()
EOF

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
