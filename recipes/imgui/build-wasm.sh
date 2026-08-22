#!/usr/bin/env bash
# recipes/imgui/build-wasm.sh — cross-compile the Dear ImGui core to
# WebAssembly via Emscripten.
#
# A SEPARATE script from build.sh on purpose: env-wasm.sh sources
# env-<host>.sh, which exports CXX=g++ and never overrides it, so the shared
# build.sh would compile NATIVE objects into libimgui.a and only fail later at
# link time with cryptic architecture errors. Here em++ / emar are named
# explicitly.
#
# wasm has no shared libraries (env-wasm.sh forces CVC_LINK=static), so this
# always produces libimgui.a. Headers + the whole backends/ tree ship as SOURCE
# exactly like the native build, so a consumer compiles the renderer glue it
# needs — cvcGL compiles backends/imgui_impl_opengl3.cpp with
# IMGUI_IMPL_OPENGL_ES3 for WebGL2.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

cd "${CVC_SOURCE_DIR}"

mkdir -p "${CVC_INSTALL_DIR}/lib" \
         "${CVC_INSTALL_DIR}/include/imgui" \
         "${CVC_INSTALL_DIR}/include/imgui/backends"

# Compile the platform-agnostic core translation units. CXXFLAGS carries
# -pthread when the prefix is a threaded one (CVC_WASM_THREADS=1); every object
# in a wasm link must agree, so it is inherited rather than hard-coded.
_srcs=( imgui.cpp imgui_draw.cpp imgui_tables.cpp imgui_widgets.cpp imgui_demo.cpp )
for _s in "${_srcs[@]}"; do
    em++ -std=c++17 -O2 -I. ${CXXFLAGS:-} -c "${_s}" -o "${_s%.cpp}.o"
done

emar rcs "${CVC_INSTALL_DIR}/lib/libimgui.a" ./*.o

# Install public headers.
cp imgui.h imconfig.h imgui_internal.h \
   imstb_rectpack.h imstb_textedit.h imstb_truetype.h \
   "${CVC_INSTALL_DIR}/include/imgui/"

# Ship the whole backends/ tree (headers, .cpp, sub-dirs) as source.
cp -R backends/. "${CVC_INSTALL_DIR}/include/imgui/backends/"

cvc_rewrite_install_paths
