#!/usr/bin/env bash
# recipes/vtk/build-wasm.sh — cross-compile VTK to wasm.
# Disables Qt support (use qt6-wasm separately) and Python wrapping.
# Rendering modules build against Emscripten's WebGL2/GLES3 backend
# (vtkWebAssemblyOpenGLRenderWindow + vtkWebAssemblyRenderWindowInteractor).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasm.sh"

# env-wasm.sh exports -pthread in C/CXX/LDFLAGS when CVC_WASM_THREADS=1; VTK
# additionally needs its own switch to size pools and enable the threaded SMP.
_vtk_threads=OFF
[[ "${CVC_WASM_THREADS:-0}" == "1" ]] && _vtk_threads=ON

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DCMAKE_FIND_ROOT_PATH="${CVC_DEPS_PREFIX}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_TOOLCHAIN_FILE="${EMSDK}/upstream/emscripten/cmake/Modules/Platform/Emscripten.cmake" \
    -DVTK_GROUP_ENABLE_Qt=NO \
    -DVTK_WRAP_PYTHON=OFF \
    -DVTK_BUILD_TESTING=OFF \
    -DVTK_BUILD_EXAMPLES=OFF \
    -DVTK_BUILD_DOCUMENTATION=OFF \
    -DVTK_LEGACY_REMOVE=ON \
    -DVTK_MODULE_ENABLE_VTK_RenderingOpenGL2=YES \
    -DVTK_MODULE_ENABLE_VTK_RenderingUI=YES \
    -DVTK_MODULE_ENABLE_VTK_RenderingVolume=YES \
    -DVTK_MODULE_ENABLE_VTK_RenderingVolumeOpenGL2=YES \
    -DVTK_MODULE_ENABLE_VTK_RenderingAnnotation=YES \
    -DVTK_MODULE_ENABLE_VTK_RenderingFreeType=YES \
    -DVTK_MODULE_ENABLE_VTK_InteractionStyle=YES \
    -DVTK_MODULE_ENABLE_VTK_IOImage=YES \
    -DVTK_MODULE_ENABLE_VTK_InteractionWidgets=DEFAULT \
    -DVTK_WEBASSEMBLY_THREADS=${_vtk_threads} \
    -DVTK_ENABLE_WRAPPING=OFF
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"
