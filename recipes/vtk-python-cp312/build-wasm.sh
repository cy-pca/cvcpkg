#!/usr/bin/env bash
# recipes/vtk-python-cp312/build-wasm.sh — GO/NO-GO SPIKE (WIP).
#
# The question this exists to answer: can VTK's Python wrappers (VTK_WRAP_PYTHON)
# be cross-compiled to wasm, STATICALLY (BUILD_SHARED_LIBS=OFF), against a wasm
# CPython — so pycvc_gl can hand back live vtkmodules objects in the browser?
# There is no prior art (VTK is absent from Pyodide; the vtk recipe ships
# VTK_WRAP_PYTHON=OFF on wasm), so this is THE make-or-break for the full
# VTK-Python bridge in the browser (VolRover Case B). If it works, expand the
# module set to the rendering build (mirror recipes/vtk/build-wasm.sh + its two
# wasm patches) and wrap that. If it does NOT, the fallback is pycvc_gl's
# no-bridge build (CVC_PYCVCGL_VTK_BRIDGE=OFF, libcvc#388).
#
# Kept DELIBERATELY MINIMAL to isolate the mechanism: a tiny module set
# (CommonCore + CommonDataModel), rendering OFF, no patches. Prove the wrap
# pipeline first; scope up second.
#
# THREE UNKNOWNS this build tests (each a real risk — adjust here as the fleet
# build teaches us):
#   [U1] VTK's wrapper GENERATORS (vtkWrapPython / vtkWrapHierarchy) are HOST
#        tools. Cross-compiling needs them built natively first, then the
#        emscripten build points VTKCompileTools_DIR at that host export. The
#        exact host-tools flag (VTK_BUILD_COMPILE_TOOLS_ONLY) and the
#        VTKCompileTools_DIR handoff are the first thing to verify against VTK 9.5.
#   [U2] Python3 detection when cross-compiling: the wrapper links against a
#        wasm libpython3.12.a (static). We hand FindPython3 explicit
#        INCLUDE_DIR/LIBRARY so it does not try to run/probe a wasm interpreter.
#   [U3] Whether VTK_WRAP_PYTHON honors BUILD_STATIC (defaults to
#        BUILD_SHARED_LIBS) and emits *static* wrapper archives with PyInit_*
#        exported, importable via PyImport_AppendInittab (VTK's generated
#        <module>_load()).
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Default CVC_JOBS: used in step 1 BEFORE env-wasm.sh is sourced, and set -u
# would otherwise abort ("CVC_JOBS: unbound variable") if the builder didn't
# export it.
: "${CVC_JOBS:=$(nproc 2>/dev/null || echo 4)}"

# ── (1) HOST compile tools — NATIVE compiler, BEFORE the wasm env ───────────
# env-wasm.sh points CC/CXX at emcc, so build the host tools first while the
# native toolchain is still in effect. [U1]
: "${CC:=gcc}"; : "${CXX:=g++}"
HOST_TOOLS="${CVC_BUILD_DIR}/host-compile-tools"
echo "vtk-python(wasm): [1/3] building VTK host compile tools (native ${CC}/${CXX})"
cmake -G Ninja -S "${CVC_SOURCE_DIR}" -B "${HOST_TOOLS}" \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_C_COMPILER="${CC}" -DCMAKE_CXX_COMPILER="${CXX}" \
    -DVTK_BUILD_COMPILE_TOOLS_ONLY=ON
cmake --build "${HOST_TOOLS}" -j "${CVC_JOBS}"

# ── (2) locate the wasm CPython (static libpython) in the dep closure ───────
# [U2] Requires python312 built for wasm to be installed in the prefix
# (cvcpkg install python312 --platform wasm, once it is published).
PY_ROOT=""
for _root in "${CVC_DEPS_PREFIX:-}" "${CVC_INSTALL_DIR}"; do
    [[ -n "${_root}" && -e "${_root}/lib/libpython3.12.a" ]] && { PY_ROOT="${_root}"; break; }
done
if [[ -z "${PY_ROOT}" ]]; then
    echo "vtk-python(wasm): no wasm libpython3.12.a found in the prefix." >&2
    echo "  Build+publish python312 for wasm first, then install it into the prefix." >&2
    exit 1
fi
PY_INC="${PY_ROOT}/include/python3.12"
PY_LIB="${PY_ROOT}/lib/libpython3.12.a"
echo "vtk-python(wasm): wrapping against wasm CPython at ${PY_ROOT}"

# [U2] VTK runs a NATIVE python of the same version during the wrapped build
# (version query, wrapper glue) — the wasm libpython is a static archive with no
# runnable interpreter. Point Python3_EXECUTABLE at the native python3.12 host
# tool; the TARGET headers/lib stay the wasm ones. Without this,
# find_package(Python3 COMPONENTS Development.Module) picks the runner's system
# python (3.10) and fails.
PY_EXE=""
for _root in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
    [[ -n "${_root}" && -x "${_root}/bin/python3.12" ]] && { PY_EXE="${_root}/bin/python3.12"; break; }
done
[[ -z "${PY_EXE}" ]] && PY_EXE="$(command -v python3.12 || true)"
if [[ -z "${PY_EXE}" ]]; then
    echo "vtk-python(wasm): no NATIVE python3.12 for Python3_EXECUTABLE (need the python312 host tool)" >&2
    exit 1
fi
echo "vtk-python(wasm): native build interpreter: ${PY_EXE}"

# ── (3) cross-build VTK to wasm WITH python wrapping, STATIC ────────────────
source "${SCRIPT_DIR}/../_common/env-wasm.sh"
echo "vtk-python(wasm): [3/3] cross-building VTK (VTK_WRAP_PYTHON=ON, static)"
cmake -G Ninja -S "${CVC_SOURCE_DIR}" -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_TOOLCHAIN_FILE="${EMSDK}/upstream/emscripten/cmake/Modules/Platform/Emscripten.cmake" \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DCMAKE_FIND_ROOT_PATH="${CVC_DEPS_PREFIX}" \
    -DVTKCompileTools_DIR="${HOST_TOOLS}" \
    -DVTK_GROUP_ENABLE_Qt=NO \
    -DVTK_WRAP_PYTHON=ON \
    -DVTK_ENABLE_WRAPPING=ON \
    -DVTK_PYTHON_VERSION=3 \
    -DPython3_FIND_STRATEGY=LOCATION \
    -DPython3_EXECUTABLE="${PY_EXE}" \
    -DPython3_INCLUDE_DIR="${PY_INC}" \
    -DPython3_LIBRARY="${PY_LIB}" \
    -DVTK_PYTHON_SITE_PACKAGES_SUFFIX="lib/python3.12/site-packages" \
    -DVTK_BUILD_TESTING=OFF -DVTK_BUILD_EXAMPLES=OFF -DVTK_BUILD_DOCUMENTATION=OFF \
    -DVTK_LEGACY_REMOVE=ON \
    -DVTK_GROUP_ENABLE_StandAlone=DONT_WANT \
    -DVTK_GROUP_ENABLE_Rendering=DONT_WANT \
    -DVTK_MODULE_ENABLE_VTK_CommonCore=YES \
    -DVTK_MODULE_ENABLE_VTK_CommonDataModel=YES
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

echo "vtk-python(wasm) GO/NO-GO result — static wrapper archives + vtkmodules:"
find "${CVC_INSTALL_DIR}" -maxdepth 5 \
     \( -name 'libvtk*Python*.a' -o -name 'vtkPythonUtil.h' -o -name 'vtkmodules' \) -print | head -20
