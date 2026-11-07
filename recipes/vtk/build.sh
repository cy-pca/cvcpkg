#!/usr/bin/env bash
# recipes/vtk/build.sh — build VTK 9.5 from source with Qt6 support.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# NetBSD: keep the base system's X11 sets (/usr/X11R7, Mesa included) out of the
# build, as qt6 does.  CMake's FindX11 searches /usr/X11R7 by name and would
# take any X library the prefix lacks from there.
CMAKE_EXTRA=()
if [[ "${CVC_PLATFORM}" == "netbsd" ]]; then
    CMAKE_EXTRA+=("-DCMAKE_IGNORE_PATH=/usr/X11R7/include;/usr/X11R7/lib")
fi

# VTK is always built shared per the existing workflow convention.
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS=ON \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DVTK_GROUP_ENABLE_Qt=YES \
    -DVTK_QT_VERSION=6 \
    -DVTK_MODULE_ENABLE_VTK_GUISupportQtQuick=NO \
    -DVTK_MODULE_ENABLE_VTK_RenderingQtQuick=NO \
    -DVTK_WRAP_PYTHON=OFF \
    -DVTK_BUILD_TESTING=OFF \
    -DVTK_BUILD_EXAMPLES=OFF \
    -DVTK_BUILD_DOCUMENTATION=OFF \
    -DVTK_LEGACY_REMOVE=ON \
    ${CMAKE_EXTRA[@]+"${CMAKE_EXTRA[@]}"}
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"
