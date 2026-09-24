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

# [U1] The host compile-tools export is 64-bit; the wasm consumer is 32-bit, so
# VTKCompileTools' generated config-version marks itself UNSUITABLE on the
# pointer-size mismatch ("9.5.0 (64bit)") and the cross build's
# find_package(VTKCompileTools) (CMake/vtkCrossCompiling.cmake, taken only when
# CMAKE_CROSSCOMPILING_EMULATOR is unset) would reject it. The wrap tools are
# host EXECUTABLES — pointer size is irrelevant to the consumer — so neutralize
# the check. When emscripten's node emulator is present the import is skipped
# entirely and this is a harmless no-op.
for _cv in $(find "${HOST_TOOLS}" -name 'vtkcompiletools-config-version.cmake' 2>/dev/null); do
    sed -i 's/set(PACKAGE_VERSION_UNSUITABLE TRUE)/set(PACKAGE_VERSION_UNSUITABLE FALSE)/' "${_cv}"
done

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

# [U2] RESOLVED — verified by reproducing VTK's REAL configure locally (an
# isolated find_package() test is misleading: VTK's module system version-locks
# the interpreter to the Development artifacts). VTK's top-level CMakeLists, on
# the VTK_WRAP_PYTHON path, runs `find_package(Python3 COMPONENTS Interpreter)`,
# which anchors on a NATIVE interpreter; the subsequent Development.Module find
# must MATCH that interpreter's version. A mismatched host interpreter (the
# runner's 3.10/3.13) makes it reject the 3.12 wasm artifacts ("missing
# Development.Module, found suitable version 3.10"). Handing explicit
# INCLUDE_DIR/LIBRARY does NOT rescue it — the interpreter version wins. So a
# native python3.12 on the build host is REQUIRED. It is used only at
# configure/wrap time (and optional .pyi generation); the wrappers still link
# the wasm libpython3.12 at the target link.
PY_EXE=""
for _cand in \
    "${CVC_BUILD_PREFIX:-}/bin/python3.12" \
    "${CVC_DEPS_PREFIX:-}/bin/python3.12" \
    "${CVC_INSTALL_DIR:-}/bin/python3.12" \
    "$(command -v python3.12 2>/dev/null || true)"; do
    [[ -n "${_cand}" && -x "${_cand}" ]] || continue
    _v="$("${_cand}" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || true)"
    [[ "${_v}" == "3.12" ]] && { PY_EXE="${_cand}"; break; }
done
# Fallback: fetch the published native python3.12 with cvcpkg directly. This
# covers fleet nodes whose INSTALLED cvcpkg predates the _collect_host_tools fix
# that resolves depends.host_tools:[python312] for the host platform (the recipe
# is pushed fresh from the branch, but the node's builder is its own). No-op when
# a native 3.12 was already found above (host-tool fix deployed).
if [[ -z "${PY_EXE}" ]]; then
    _hp="$(uname -s 2>/dev/null || echo Linux)"
    case "${_hp}" in Linux) _hp=linux;; Darwin) _hp=macos;; *) _hp=linux;; esac
    _ha="$(uname -m 2>/dev/null || echo x86_64)"
    case "${_ha}" in x86_64|amd64) _ha=x86_64;; arm64|aarch64) _ha=arm64;; esac
    _hostpy="${CVC_BUILD_DIR}/hostpy312"
    _cvc="cvcpkg"; command -v cvcpkg >/dev/null 2>&1 || _cvc="python3 -m cvcpkg"
    echo "vtk-python(wasm): no native 3.12 in prefix; provisioning via cvcpkg (${_hp}/${_ha})"
    ${_cvc} install python312 --platform "${_hp}" --arch "${_ha}" \
        --config release --link shared --prefix "${_hostpy}" --no-fallback-to-source >&2 || \
        echo "vtk-python(wasm): 'cvcpkg install python312' (host) failed; see diagnostics below" >&2
    for _c in "${_hostpy}/bin/python3.12" "${_hostpy}/bin/python3"; do
        [[ -x "${_c}" ]] || continue
        _v="$("${_c}" -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null || true)"
        [[ "${_v}" == "3.12" ]] && { PY_EXE="${_c}"; break; }
    done
    [[ -n "${PY_EXE}" ]] && echo "vtk-python(wasm): provisioned native 3.12 at ${PY_EXE}"
fi
if [[ -z "${PY_EXE}" ]]; then
    echo "vtk-python(wasm): FATAL — no native python3.12 on the build host." >&2
    echo "  VTK's wrap configure anchors find_package(Python3 Interpreter) on a native" >&2
    echo "  interpreter whose version must match the 3.12 target artifacts, so a native" >&2
    echo "  3.12 is required (declare python312 as a host tool that resolves for the HOST" >&2
    echo "  platform). Diagnostics:" >&2
    echo "    CVC_BUILD_PREFIX=${CVC_BUILD_PREFIX:-<unset>}" >&2
    echo "    CVC_DEPS_PREFIX=${CVC_DEPS_PREFIX:-<unset>}" >&2
    for _d in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}" "${CVC_INSTALL_DIR:-}"; do
        [[ -n "${_d}" && -d "${_d}/bin" ]] && ls -1 "${_d}/bin" 2>/dev/null | grep -i '^python' | sed "s|^|      ${_d}/bin/|" >&2
    done
    echo "    PATH python3*: $(command -v python3 python3.12 python3.13 2>/dev/null | tr '\n' ' ')" >&2
    exit 1
fi
echo "vtk-python(wasm): native build interpreter (3.12): ${PY_EXE}"

# FindPython3 knobs: LOCATION strategy + a matching native interpreter + the
# explicit wasm TARGET artifacts (headers/lib stay 3.12 for the wasm link).
PYFIND_ARGS=(
    -DPython3_FIND_STRATEGY=LOCATION
    -DPython3_EXECUTABLE="${PY_EXE}"
    -DPython3_INCLUDE_DIR="${PY_INC}"
    -DPython3_LIBRARY="${PY_LIB}"
)

# ── (3) cross-build VTK to wasm WITH python wrapping, STATIC ────────────────
source "${SCRIPT_DIR}/../_common/env-wasm.sh"
echo "vtk-python(wasm): [3/3] cross-building VTK (VTK_WRAP_PYTHON=ON, static)"
# VTK's threaded-wasm switch (pool sizing + threaded SMP backend), from the flavor
# hook — must match the -pthread the rest of the wasm-mt closure is built with.
_vtk_threads=OFF
[[ "${CVC_WASM_THREADS:-0}" == "1" ]] && _vtk_threads=ON
# Force VTK to import our NATIVE host wrap tools (VTKCompileTools_DIR) rather than
# building wasm wrap tools that run under node: the wasm tools cannot read the
# host filesystem (@argfiles/headers) under emscripten's node FS and abort with a
# usage message (this is why VTK wasm Python-wrapping has no prior art). VTK
# builds wasm tools only when CMAKE_CROSSCOMPILING_EMULATOR is defined, which the
# emscripten toolchain (Emscripten.cmake) sets iff find_program(node) succeeds on
# PATH. Hiding node for the CONFIGURE leaves the emulator undefined, so
# vtkCrossCompiling.cmake imports VTKCompileTools (native). emcc still finds node
# via EMSDK_NODE for any internal need, and we never RUN wasm at build time
# (native host-tool generation + the pre-seeded LFS try_run). This mirrors the
# verified local build (whose configure also had node off PATH).
_PATH_NONODE="$(printf '%s' "${PATH}" | tr ':' '\n' | grep -v '/node/' | tr '\n' ':' | sed 's/:$//')"
env PATH="${_PATH_NONODE}" cmake -G Ninja -S "${CVC_SOURCE_DIR}" -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_TOOLCHAIN_FILE="${EMSDK}/upstream/emscripten/cmake/Modules/Platform/Emscripten.cmake" \
    -DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}" \
    -DCMAKE_FIND_ROOT_PATH="${CVC_DEPS_PREFIX}" \
    -DVTKCompileTools_DIR="${HOST_TOOLS}" \
    -DVTK_REQUIRE_LARGE_FILE_SUPPORT_EXITCODE=0 \
    -DVTK_REQUIRE_LARGE_FILE_SUPPORT_EXITCODE__TRYRUN_OUTPUT= \
    -DVTK_GROUP_ENABLE_Qt=NO \
    -DVTK_WRAP_PYTHON=ON \
    -DVTK_ENABLE_WRAPPING=ON \
    -DVTK_PYTHON_VERSION=3 \
    -DVTK_INSTALL_PYTHON_EXES=OFF \
    "${PYFIND_ARGS[@]}" \
    -DVTK_PYTHON_SITE_PACKAGES_SUFFIX="lib/python3.12/site-packages" \
    -DVTK_BUILD_TESTING=OFF -DVTK_BUILD_EXAMPLES=OFF -DVTK_BUILD_DOCUMENTATION=OFF \
    -DVTK_LEGACY_REMOVE=ON \
    -DVTK_GROUP_ENABLE_StandAlone=DONT_WANT \
    -DVTK_GROUP_ENABLE_Rendering=DONT_WANT \
    -DVTK_MODULE_ENABLE_VTK_hdf5=YES \
    -DVTK_MODULE_USE_EXTERNAL_VTK_hdf5=ON \
    -DVTK_MODULE_ENABLE_VTK_IOHDF=YES \
    -DVTK_MODULE_ENABLE_VTK_netcdf=NO \
    -DVTK_MODULE_ENABLE_VTK_IONetCDF=NO \
    -DVTK_MODULE_ENABLE_VTK_IOExodus=NO \
    -DVTK_MODULE_ENABLE_VTK_CommonColor=YES \
    -DVTK_MODULE_ENABLE_VTK_CommonComputationalGeometry=YES \
    -DVTK_MODULE_ENABLE_VTK_FiltersCore=YES \
    -DVTK_MODULE_ENABLE_VTK_FiltersGeneral=YES \
    -DVTK_MODULE_ENABLE_VTK_FiltersSources=YES \
    -DVTK_MODULE_ENABLE_VTK_FiltersGeometry=YES \
    -DVTK_MODULE_ENABLE_VTK_FiltersModeling=YES \
    -DVTK_MODULE_ENABLE_VTK_FiltersExtraction=YES \
    -DVTK_MODULE_ENABLE_VTK_FiltersHybrid=YES \
    -DVTK_MODULE_ENABLE_VTK_FiltersGeometryPreview=YES \
    -DVTK_MODULE_ENABLE_VTK_FiltersTexture=YES \
    -DVTK_MODULE_ENABLE_VTK_ImagingCore=YES \
    -DVTK_MODULE_ENABLE_VTK_ImagingGeneral=YES \
    -DVTK_MODULE_ENABLE_VTK_ImagingSources=YES \
    -DVTK_MODULE_ENABLE_VTK_IOXML=YES \
    -DVTK_MODULE_ENABLE_VTK_IOGeometry=YES \
    -DVTK_MODULE_ENABLE_VTK_IOLegacy=YES \
    -DVTK_MODULE_ENABLE_VTK_IOPLY=YES \
    -DVTK_MODULE_ENABLE_VTK_IOImage=YES \
    -DVTK_MODULE_ENABLE_VTK_RenderingCore=YES \
    -DVTK_MODULE_ENABLE_VTK_RenderingOpenGL2=YES \
    -DVTK_MODULE_ENABLE_VTK_RenderingUI=YES \
    -DVTK_MODULE_ENABLE_VTK_RenderingVolume=YES \
    -DVTK_MODULE_ENABLE_VTK_RenderingVolumeOpenGL2=YES \
    -DVTK_MODULE_ENABLE_VTK_RenderingAnnotation=YES \
    -DVTK_MODULE_ENABLE_VTK_RenderingFreeType=YES \
    -DVTK_MODULE_ENABLE_VTK_InteractionStyle=YES \
    -DVTK_MODULE_ENABLE_VTK_InteractionWidgets=YES \
    -DVTK_WEBASSEMBLY_THREADS=${_vtk_threads}
# FULL wrap set (cvc.4 — "all the way"): StandAlone=WANT wraps the ENTIRE backend-free
# data API (Common/Filters/IO/Imaging/Infovis), plus the base vtk recipe's proven
# wasm RENDERING set (RenderingOpenGL2/Volume/VolumeOpenGL2/FreeType/Annotation/UI +
# InteractionStyle/Widgets) on the WebGL2/GLES3 backend. This gives pycvc_gl the full
# vtk-python API in the browser: data objects, filters, readers/writers, AND live
# render windows / actors / GPU volume mappers. WANT enables only what builds on wasm
# (external-dependency IO modules auto-skip); the -k 0 below tolerates any peripheral
# module whose wrapper does not build, and the _missing check guards the core set.
# Build keep-going (-k 0): the standalone `vtkpython` interpreter executable is
# EXPECTED to fail to link on wasm — libpython3.12.a's _decimal.o references
# mpd_isspecial (libmpdecimal is not archived into the wasm CPython). We do not
# ship that CLI (VTK_INSTALL_PYTHON_EXES=OFF); every wrapper archive we package
# builds fine. Keep-going lets the rest (vtkWrappingTools, the C++ libs, all
# *Python.a + _vtkmodules_static.a) complete despite that one exe link error.
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}" -- -k 0 || true
# A REAL failure must still abort: verify the packaged wrapper archives exist
# before installing (only the vtkpython exe is permitted to be missing).
_missing=""
for _pat in "_vtkmodules_static.a" "libvtkWrappingPythonCore*.a" "libvtkCommonCorePython.a" "libvtkCommonDataModelPython.a" "libvtkRenderingCorePython.a" "libvtkRenderingOpenGL2Python.a" "libvtkFiltersCorePython.a" "libvtkIOGeometryPython.a"; do
    compgen -G "${CVC_BUILD_DIR}/lib/${_pat}" >/dev/null 2>&1 || _missing="${_missing} ${_pat}"
done
if [ -n "${_missing}" ]; then
    echo "vtk-python(wasm): FATAL — wrapper archive(s) missing after build:${_missing}" >&2
    echo "  (only the vtkpython interpreter exe is permitted to fail on wasm)" >&2
    exit 1
fi
cmake --install "${CVC_BUILD_DIR}"

# ── PRUNE to python-only artifacts (CRITICAL) ───────────────────────────────
# stage_bundle (builder.py) ships the ENTIRE install tree — package.files is
# declarative, NOT a filter. Without this prune vtk-python would ship a full VTK
# that FILE-CONFLICTS with the `vtk` package (whose C++ libs the wrappers link at
# the consumer's final link). Keep ONLY the Python wrapper artifacts. For the
# wasm STATIC build the python stubs ship as `_vtk.zip` (VTK's static-python
# bundle, not an unpacked vtkmodules/) and the static module aggregate is
# `_vtkmodules_static.a`; mirror recipes/vtk-python-cp312/build.sh otherwise.
_keep() {
  for _f in $1; do
    [ -e "${_f}" ] || continue
    mkdir -p "${_KEEP}/$(dirname "${_f}")"
    cp -a "${_f}" "${_KEEP}/${_f}"
  done
}
_KEEP="$(mktemp -d)"
cd "${CVC_INSTALL_DIR}"
_keep 'lib/libvtk*Python*'
_keep 'lib/_vtkmodules_static.a'
_keep 'lib/python*/site-packages/_vtk.zip'
_keep 'lib/python*/site-packages/vtkmodules'
_keep 'lib/python*/site-packages/vtk.py'
_keep 'include/vtk-9.5/*Python*.h'
_keep 'include/vtk-9.5/PyVTK*.h'
_keep 'include/vtk-9.5/vtkSmartPyObject.h'
find "${CVC_INSTALL_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
cp -a "${_KEEP}/." "${CVC_INSTALL_DIR}/"
rm -rf "${_KEEP}"
echo "vtk-python(wasm): pruned to python-only artifacts:"
find "${CVC_INSTALL_DIR}" \( -name 'libvtk*Python*.a' -o -name '_vtkmodules_static.a' \
     -o -name '_vtk.zip' -o -name 'vtkPythonUtil.h' \) -print | sort | head -20
