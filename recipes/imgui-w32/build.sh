#!/usr/bin/env bash
# recipes/imgui-w32/build.sh — compile the Dear ImGui core, with its 32-bit ImWchar, into
# libimgui-w32 and install headers + backend sources under include/imgui-w32.
#
# The imgui recipe's build.sh under names of its own, so this package and imgui never collide:
# ImGui ships no CMake install, so we compile the core translation units by hand.
# CVC_LINK=static → a static archive (libimgui-w32.a);
# CVC_LINK=shared → a shared library (libimgui-w32.so / libimgui-w32.dylib).
# The backends/ directory is shipped as SOURCE (headers + .cpp/.mm) so the
# consumer compiles the platform/renderer glue it actually needs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

_inc="${CVC_INSTALL_DIR}/include/imgui-w32"
mkdir -p "${CVC_INSTALL_DIR}/lib" "${_inc}/backends"

# Compile the platform-agnostic core translation units. imconfig.h (patched) defines
# IMGUI_USE_WCHAR32, so the objects and every consumer of the installed headers agree.
_srcs=( imgui.cpp imgui_draw.cpp imgui_tables.cpp imgui_widgets.cpp imgui_demo.cpp )
for _s in "${_srcs[@]}"; do
    "${CXX}" -std=c++17 -fPIC -O2 -I. -c "${_s}" -o "${_s%.cpp}.o"
done

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    ar rcs "${CVC_INSTALL_DIR}/lib/libimgui-w32.a" ./*.o
    _imgui_lib=libimgui-w32.a
elif [[ "${CVC_PLATFORM}" == "macos" ]]; then
    "${CXX}" -dynamiclib -install_name "@rpath/libimgui-w32.dylib" \
        -o "${CVC_INSTALL_DIR}/lib/libimgui-w32.dylib" ./*.o
    _imgui_lib=libimgui-w32.dylib
else
    "${CXX}" -shared -Wl,-soname,libimgui-w32.so \
        -o "${CVC_INSTALL_DIR}/lib/libimgui-w32.so" ./*.o
    _imgui_lib=libimgui-w32.so
fi

# Install public headers.
cp imgui.h imconfig.h imgui_internal.h \
   imstb_rectpack.h imstb_textedit.h imstb_truetype.h \
   "${_inc}/"

# Ship the whole backends/ tree (headers, .cpp, .mm, sub-dirs) as source.
cp -R backends/. "${_inc}/backends/"

# And the FreeType font loader, as source too: a consumer that links FreeType compiles it.
mkdir -p "${_inc}/misc/freetype"
cp misc/freetype/imgui_freetype.h misc/freetype/imgui_freetype.cpp "${_inc}/misc/freetype/"

# A relocatable cmake package config: find_package(imgui-w32), the imgui-w32::imgui imported
# target. The prefix is derived from the file's own location at configure time, so it survives
# cvcpkg's path relocation with nothing to rewrite.
bash "${SCRIPT_DIR}/write-cmake-config.sh" "${CVC_INSTALL_DIR}" "${_imgui_lib}"

cvc_rewrite_install_paths
