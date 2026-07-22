#!/usr/bin/env bash
# recipes/vtk-python/build.sh — rebuild VTK 9.5 from the SAME pinned source as
# the `vtk` recipe but with -DVTK_WRAP_PYTHON=ON, then package ONLY the Python
# wrapper artifacts (vtkmodules/, libvtkWrappingPythonCore / libvtkPythonInterpreter,
# and the vtkPython*/PyVTK* bridge headers). The C++ libraries + CMake config come
# from the `vtk` package; the two install side by side in one prefix with no file
# overlap (the `vtk` recipe builds WRAP_PYTHON=OFF and emits none of these files).
#
# ABI CONTRACT: this recipe MUST track `vtk` exactly — same upstream_version, same
# toolchain, same C++ module flags — so the wrapper .so's are ABI-compatible with
# the vtk package's libvtk*-9.5.so at runtime. Keep the C++ cmake flags below
# byte-for-byte identical to recipes/vtk/build.sh; bump both cvc_revisions together.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# Locate the cvcpkg python312 interpreter in the dependency closure so the
# wrappers are built against the SAME Python (cp312) that pycvc / pycvc-gl use.
PY_EXE=""
for _root in "${CVC_DEPS_PREFIX:-}" "${CVC_BUILD_PREFIX:-}" "${CVC_INSTALL_DIR}"; do
    [[ -n "${_root}" ]] || continue
    if [[ -x "${_root}/bin/python3.12" ]]; then PY_EXE="${_root}/bin/python3.12"; break; fi
done
if [[ -z "${PY_EXE}" ]]; then
    echo "vtk-python: could not find python3.12 in the dependency closure" >&2
    exit 1
fi
PY_ROOT="$(cd "$(dirname "${PY_EXE}")/.." && pwd)"
echo "vtk-python: wrapping against ${PY_EXE}"

# The C++ configuration here is intentionally IDENTICAL to recipes/vtk/build.sh
# (same modules, same Qt) so the wrapped ABI matches the `vtk` package; only
# VTK_WRAP_PYTHON and the Python-finding/site-packages options are added.
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
    -DVTK_WRAP_PYTHON=ON \
    -DVTK_PYTHON_VERSION=3 \
    -DPython3_EXECUTABLE="${PY_EXE}" \
    -DPython3_ROOT_DIR="${PY_ROOT}" \
    -DPython3_FIND_STRATEGY=LOCATION \
    -DVTK_PYTHON_SITE_PACKAGES_SUFFIX="lib/python3.12/site-packages" \
    -DVTK_BUILD_TESTING=OFF \
    -DVTK_BUILD_EXAMPLES=OFF \
    -DVTK_BUILD_DOCUMENTATION=OFF \
    -DVTK_LEGACY_REMOVE=ON
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

# Make the shipped wrapper .so's relocatable (find the vtk package's
# libvtk*-9.5.so via the prefix lib dir) and rewrite any absolute install-dir
# paths baked into shipped files. No-op where the helper isn't defined.
if command -v cvc_rewrite_install_paths >/dev/null 2>&1; then
    cvc_rewrite_install_paths || true
fi

# ── PRUNE to python-only artifacts (CRITICAL) ───────────────────────────────
# stage_bundle (builder.py) ships the ENTIRE install tree — `package.files` is
# NOT a filter (it is parsed but unused). Without this prune, vtk-python would
# ship a full, divergent VTK that FILE-CONFLICTS with the `vtk` package and
# defeats the whole point of the split. Keep ONLY the Python wrapper artifacts
# (the `package.files` set); everything else is owned by the `vtk` package.
#
# HERMETICITY NOTE: even pruned, the wrappers here are compiled against THIS
# recipe's from-scratch VTK C++ rebuild, not the exact `.so` bytes the `vtk`
# package ships. On a consistent fleet toolchain they are ABI-matched, but the
# fully hermetic design is to WRAP THE INSTALLED vtk (VTK 9.5 supports it:
# vtk_module_wrap_python has an imported-target path and the vtk package already
# ships the hierarchy files + vtkModuleWrapPython.cmake). That path additionally
# requires building VTK::WrappingPythonCore / PythonInterpreter from the wrapping
# sources against the installed VTK — a follow-up. (The recipe.yaml's earlier
# claim that installed VTK cannot be wrapped is incorrect.)
_keep() { # copy $1 (glob, relative to install dir) into the keep-stage if present
  for _f in $1; do
    [ -e "${_f}" ] || continue
    mkdir -p "${_KEEP}/$(dirname "${_f}")"
    cp -a "${_f}" "${_KEEP}/${_f}"
  done
}
_KEEP="$(mktemp -d)"
cd "${CVC_INSTALL_DIR}"
_keep 'lib/libvtk*Python*'
_keep 'lib/vtk*Python*.lib'
_keep 'bin/vtk*Python*'
_keep 'lib/python*/site-packages/vtkmodules'
_keep 'lib/python*/site-packages/vtk.py'
_keep 'Lib/site-packages/vtkmodules'
_keep 'Lib/site-packages/vtk.py'
_keep 'include/vtk-9.5/*Python*.h'
_keep 'include/vtk-9.5/PyVTK*.h'
_keep 'include/vtk-9.5/vtkSmartPyObject.h'
# Replace the install tree with only the kept python artifacts.
find "${CVC_INSTALL_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
cp -a "${_KEEP}/." "${CVC_INSTALL_DIR}/"
rm -rf "${_KEEP}"
echo "vtk-python: pruned to python-only artifacts:"
find "${CVC_INSTALL_DIR}" -maxdepth 3 \( -name 'libvtk*Python*' -o -name 'vtkPythonUtil.h' -o -name 'vtkmodules' \) | head
