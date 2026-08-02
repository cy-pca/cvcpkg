#!/usr/bin/env bash
# recipes/pyside6/build.sh — build the sources/pyside6 CMake project from the
# pinned Qt-for-Python pyside-setup 6.8.2 tarball, against the cvcpkg python311
# interpreter, the cvcpkg qt6 6.8.2, and the already-installed cvcpkg shiboken6.
# The shiboken6 generator (which parses Qt headers with the SYSTEM libclang-18)
# emits the C++ bindings; cmake compiles + installs the PySide6 module.
#
# NO-BUNDLE CONTRACT (the reason this recipe exists): -DSTANDALONE=0 links the
# cvcpkg qt6 by soname and copies NO Qt libraries into the install tree, so the
# embedded interpreter shares the host app's single libQt6Core — the precondition
# for shiboken6.wrapInstance(addr, QtWidgets.QMainWindow) on the live QMainWindow.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/python-wheel.sh"

# ── Locate the cvcpkg python3.11 interpreter in the dependency closure ───────
PY_EXE=""
for _root in "${CVC_DEPS_PREFIX:-}" "${CVC_BUILD_PREFIX:-}" "${CVC_INSTALL_DIR}"; do
    [[ -n "${_root}" ]] || continue
    if [[ -x "${_root}/bin/python3.11" ]]; then PY_EXE="${_root}/bin/python3.11"; break; fi
done
if [[ -z "${PY_EXE}" ]]; then
    echo "pyside6: could not find python3.11 in the dependency closure" >&2
    exit 1
fi
PY_ROOT="$(cd "$(dirname "${PY_EXE}")/.." && pwd)"
echo "pyside6: building against ${PY_EXE}"

# ── Hermetic libclang 18 (cvcpkg llvm18) — the shiboken6 generator runs during
# THIS build to parse Qt headers and links libclang. Prefer the cvcpkg llvm18 in
# the dependency prefix (pulled in transitively via shiboken6); system
# /usr/lib/llvm-18 only as a last-resort fallback.
if [[ -z "${LLVM_INSTALL_DIR:-}" ]]; then
    for _llvm in "${CVC_DEPS_PREFIX:-}" "${CVC_BUILD_PREFIX:-}" "${CVC_INSTALL_DIR}"; do
        if [[ -n "${_llvm}" && -f "${_llvm}/lib/cmake/clang/ClangConfig.cmake" ]]; then
            LLVM_INSTALL_DIR="${_llvm}"; break
        fi
    done
    : "${LLVM_INSTALL_DIR:=/usr/lib/llvm-18}"
fi
export LLVM_INSTALL_DIR
echo "pyside6: LLVM_INSTALL_DIR=${LLVM_INSTALL_DIR}"

# The generator (bin/shiboken6, from the shiboken6 package) needs to resolve, at
# RUN time during this build: libclang (cvcpkg llvm18), libQt6Core (cvcpkg qt6),
# libshiboken6 (cvcpkg shiboken6). Put all three lib dirs on the loader path.
export LD_LIBRARY_PATH="${LLVM_INSTALL_DIR}/lib:${CVC_DEPS_PREFIX:-}/lib:${CVC_BUILD_PREFIX:-}/lib:${LD_LIBRARY_PATH:-}"

# Install the importable package into THIS prefix's site-packages.
SITE_PACKAGES="${CVC_INSTALL_DIR}/lib/python3.11/site-packages"

# (The Qt 6.8 arm_acle x86 workaround that used to live here is gone: qt6 +cvc.6
# ships an __ARM_ACLE-gated qyieldcpu.h via qyieldcpu-arm-acle-include.patch, so
# the shiboken generator parses the Qt headers cleanly on x86 with no patching.)

# Module subset — ONLY modules the feature-lean cvcpkg qt6 provides
# (Core, Gui, Widgets, OpenGL, OpenGLWidgets; no Qml/Quick/Sql, offscreen-only).
PYSIDE_MODULES="Core;Gui;Widgets;OpenGL;OpenGLWidgets"

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}/sources/pyside6" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DCMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH:-${CVC_DEPS_PREFIX}}" \
    -DPython_EXECUTABLE="${PY_EXE}" \
    -DPython_ROOT_DIR="${PY_ROOT}" \
    -DPython_FIND_STRATEGY=LOCATION \
    -DPYTHON_SITE_PACKAGES="${SITE_PACKAGES}" \
    -DMODULES="${PYSIDE_MODULES}" \
    -DSTANDALONE=0 \
    -DFORCE_LIMITED_API=yes \
    -DBUILD_TESTS=OFF \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_INSTALL_RPATH="\$ORIGIN;\$ORIGIN/../lib" \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

# Make .pc/.cmake files relocatable (rewrite absolute install-dir paths).
cvc_rewrite_install_paths

# Per-interpreter column: the built PySide6 tree stays in python3.11's
# site-packages only. Further columns (pyside6-cp312, ...) are their own
# recipes built against their own interpreter.
