#!/usr/bin/env bash
# recipes/imgui/build.sh — compile the Dear ImGui core into libimgui and
# install headers + backend sources.
#
# ImGui ships no CMake install, so we compile the core translation units by
# hand.  CVC_LINK=static  → a static archive (libimgui.a);
# CVC_LINK=shared → a shared library (libimgui.so / libimgui.dylib).
# The backends/ directory is shipped as SOURCE (headers + .cpp/.mm) so the
# consumer compiles the platform/renderer glue it actually needs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

mkdir -p "${CVC_INSTALL_DIR}/lib" \
         "${CVC_INSTALL_DIR}/include/imgui" \
         "${CVC_INSTALL_DIR}/include/imgui/backends"

# Compile the platform-agnostic core translation units.
_srcs=( imgui.cpp imgui_draw.cpp imgui_tables.cpp imgui_widgets.cpp imgui_demo.cpp )
for _s in "${_srcs[@]}"; do
    "${CXX}" -std=c++17 -fPIC -O2 -I. -c "${_s}" -o "${_s%.cpp}.o"
done

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    ar rcs "${CVC_INSTALL_DIR}/lib/libimgui.a" ./*.o
elif [[ "${CVC_PLATFORM}" == "macos" ]]; then
    "${CXX}" -dynamiclib -install_name "@rpath/libimgui.dylib" \
        -o "${CVC_INSTALL_DIR}/lib/libimgui.dylib" ./*.o
else
    "${CXX}" -shared -Wl,-soname,libimgui.so \
        -o "${CVC_INSTALL_DIR}/lib/libimgui.so" ./*.o
fi

# Install public headers.
cp imgui.h imconfig.h imgui_internal.h \
   imstb_rectpack.h imstb_textedit.h imstb_truetype.h \
   "${CVC_INSTALL_DIR}/include/imgui/"

# Ship the whole backends/ tree (headers, .cpp, .mm, sub-dirs) as source.
cp -R backends/. "${CVC_INSTALL_DIR}/include/imgui/backends/"

cvc_rewrite_install_paths
