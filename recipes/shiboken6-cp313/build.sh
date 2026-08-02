#!/usr/bin/env bash
# recipes/shiboken6/build.sh — build the sources/shiboken6 CMake project from the
# pinned Qt-for-Python pyside-setup 6.8.2 tarball, against the cvcpkg python313
# interpreter + cvcpkg qt6 6.8.2 and the SYSTEM libclang-18 (NOT the cvcpkg
# `llvm` recipe, which is LLVM 22 — too new for shiboken 6.8.2).
#
# Produces: bin/shiboken6 (generator, links QtCore), lib/libshiboken6*.so,
# the importable cp313 `shiboken6` package (wrapInstance), and the Shiboken6
# CMake config the `pyside6` recipe consumes.
#
# NO-BUNDLE CONTRACT: building the CMake project directly (not setup.py
# --standalone) means Qt and libclang are linked by soname and never copied into
# the install tree — one libQt6Core in-process with the host app.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/python-wheel.sh"

# ── Locate the cvcpkg python3.13 interpreter in the dependency closure ───────
# (same pattern as recipes/vtk-python-cp313/build.sh) so the bindings + module
# are built against the SAME cp313 the host embeds.
PY_EXE=""
for _root in "${CVC_DEPS_PREFIX:-}" "${CVC_BUILD_PREFIX:-}" "${CVC_INSTALL_DIR}"; do
    [[ -n "${_root}" ]] || continue
    if [[ -x "${_root}/bin/python3.13" ]]; then PY_EXE="${_root}/bin/python3.13"; break; fi
done
if [[ -z "${PY_EXE}" ]]; then
    echo "shiboken6: could not find python3.13 in the dependency closure" >&2
    exit 1
fi
PY_ROOT="$(cd "$(dirname "${PY_EXE}")/.." && pwd)"
echo "shiboken6: building against ${PY_EXE}"

# ── Hermetic libclang 18 (cvcpkg llvm18) for ApiExtractor ────────────────────
# shiboken's setup_clang() reads LLVM_INSTALL_DIR to find ClangConfig.cmake +
# libclang and, at generation time, the Clang builtin/resource headers under
# ${LLVM_INSTALL_DIR}/lib/clang/<ver>/include. Prefer the cvcpkg llvm18 in the
# dependency prefix (a hermetic dep of this recipe); fall back to a system
# /usr/lib/llvm-18 only if a caller hasn't set LLVM_INSTALL_DIR and llvm18 is
# somehow absent.
if [[ -z "${LLVM_INSTALL_DIR:-}" ]]; then
    for _llvm in "${CVC_DEPS_PREFIX:-}" "${CVC_BUILD_PREFIX:-}" "${CVC_INSTALL_DIR}"; do
        if [[ -n "${_llvm}" && -f "${_llvm}/lib/cmake/clang/ClangConfig.cmake" ]]; then
            LLVM_INSTALL_DIR="${_llvm}"; break
        fi
    done
    : "${LLVM_INSTALL_DIR:=/usr/lib/llvm-18}"
fi
export LLVM_INSTALL_DIR
echo "shiboken6: LLVM_INSTALL_DIR=${LLVM_INSTALL_DIR}"
if [[ ! -f "${LLVM_INSTALL_DIR}/lib/cmake/clang/ClangConfig.cmake" ]]; then
    echo "shiboken6: WARNING ClangConfig.cmake not under ${LLVM_INSTALL_DIR} — is llvm18 in the closure?" >&2
fi
if [[ -z "$(find "${LLVM_INSTALL_DIR}/lib/clang" -maxdepth 2 -name stddef.h 2>/dev/null | head -1)" ]]; then
    echo "shiboken6: WARNING Clang builtin headers (…/lib/clang/*/include) missing under ${LLVM_INSTALL_DIR}" >&2
fi
# Ensure the freshly-built generator can resolve libclang/libQt6Core/libshiboken
# if it is invoked during this build (and to keep the env consistent with the
# pyside6 build where the generator actually runs).
export LD_LIBRARY_PATH="${LLVM_INSTALL_DIR}/lib:${CVC_DEPS_PREFIX:-}/lib:${LD_LIBRARY_PATH:-}"

# Install the importable package into THIS prefix's site-packages (not the
# python313 interpreter's own prefix). shiboken otherwise prefixes
# CMAKE_INSTALL_PREFIX itself, but we pin it explicitly for determinism.
SITE_PACKAGES="${CVC_INSTALL_DIR}/lib/python3.13/site-packages"

cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}/sources/shiboken6" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_INSTALL_PREFIX="${CVC_INSTALL_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DCMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH:-${CVC_DEPS_PREFIX}}" \
    -DPython_EXECUTABLE="${PY_EXE}" \
    -DPython_ROOT_DIR="${PY_ROOT}" \
    -DPython_FIND_STRATEGY=LOCATION \
    -DPYTHON_SITE_PACKAGES="${SITE_PACKAGES}" \
    -DSHIBOKEN_BUILD_TOOLS=ON \
    -DSHIBOKEN_BUILD_LIBS=ON \
    -DFORCE_LIMITED_API=yes \
    -DBUILD_TESTS=OFF \
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DCMAKE_INSTALL_RPATH="\$ORIGIN;\$ORIGIN/../lib" \
    -DCMAKE_BUILD_WITH_INSTALL_RPATH=ON
cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

# Make .pc/.cmake files relocatable (rewrite absolute install-dir paths).
cvc_rewrite_install_paths

# Per-interpreter column: the built shiboken6 tree stays in python3.13's
# site-packages only. Further columns (shiboken6-cp312, ...) are their own
# recipes built against their own interpreter.
