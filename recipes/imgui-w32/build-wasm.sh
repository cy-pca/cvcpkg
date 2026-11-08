#!/usr/bin/env bash
# recipes/imgui-w32/build-wasm.sh — cross-compile the Dear ImGui core, with its 32-bit ImWchar,
# to WebAssembly via Emscripten: libimgui-w32.a, headers under include/imgui-w32.
#
# A SEPARATE script from build.sh on purpose: env-wasm.sh sources
# env-<host>.sh, which exports CXX=g++ and never overrides it, so the shared
# build.sh would compile NATIVE objects into the archive and only fail later at
# link time with cryptic architecture errors. Here em++ / emar are named
# explicitly.
#
# wasm has no shared libraries (env-wasm.sh forces CVC_LINK=static), so this
# always produces libimgui-w32.a. Headers + the whole backends/ tree ship as SOURCE
# exactly like the native build, so a consumer compiles the renderer glue it
# needs — cvcGL compiles backends/imgui_impl_opengl3.cpp with
# IMGUI_IMPL_OPENGL_ES3 for WebGL2.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

_inc="${CVC_INSTALL_DIR}/include/imgui-w32"
mkdir -p "${CVC_INSTALL_DIR}/lib" "${_inc}/backends"

# Compile the platform-agnostic core translation units. CXXFLAGS carries
# -pthread when the prefix is a threaded one (CVC_WASM_THREADS=1); every object
# in a wasm link must agree, so it is inherited rather than hard-coded.
_srcs=( imgui.cpp imgui_draw.cpp imgui_tables.cpp imgui_widgets.cpp imgui_demo.cpp )
for _s in "${_srcs[@]}"; do
    em++ -std=c++17 -O2 -I. ${CXXFLAGS:-} -c "${_s}" -o "${_s%.cpp}.o"
done

emar rcs "${CVC_INSTALL_DIR}/lib/libimgui-w32.a" ./*.o

# Install public headers.
cp imgui.h imconfig.h imgui_internal.h \
   imstb_rectpack.h imstb_textedit.h imstb_truetype.h \
   "${_inc}/"

# Ship the whole backends/ tree (headers, .cpp, sub-dirs) as source.
cp -R backends/. "${_inc}/backends/"

# And the FreeType font loader, as source too: a consumer that links FreeType compiles it.
mkdir -p "${_inc}/misc/freetype"
cp misc/freetype/imgui_freetype.h misc/freetype/imgui_freetype.cpp "${_inc}/misc/freetype/"

# The same relocatable cmake package config as build.sh's, for libimgui-w32.a.
bash "${SCRIPT_DIR}/write-cmake-config.sh" "${CVC_INSTALL_DIR}" libimgui-w32.a

cvc_rewrite_install_paths
