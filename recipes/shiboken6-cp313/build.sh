#!/usr/bin/env bash
# recipes/shiboken6/build.sh — build the sources/shiboken6 CMake project from the
# pinned Qt-for-Python pyside-setup 6.8.2 tarball, against the cvcpkg python313
# interpreter + cvcpkg qt6 6.8.2 and the cvcpkg llvm18 libclang (NOT the cvcpkg
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

# ── libclang 18 from the cvcpkg llvm18 dependency, and from nowhere else ─────
# shiboken's setup_clang() reads LLVM_INSTALL_DIR to find ClangConfig.cmake and
# libclang. The generator reads it again at run time to find Clang's builtin
# (resource) headers under ${LLVM_INSTALL_DIR}/lib/clang/<ver>/include, and it
# runs during this build: shibokenmodule's wrappers are generated with it.
# llvm18 is a declared dependency, so it is in the runtime closure
# (CVC_DEPS_PREFIX) or the build closure (CVC_BUILD_PREFIX). Look there only:
# no /usr/lib/llvm-*, no Homebrew keg, no llvm-config on PATH, and no
# LLVM_INSTALL_DIR / CLANG_INSTALL_DIR inherited from the caller.
unset LLVM_INSTALL_DIR CLANG_INSTALL_DIR
for _llvm in "${CVC_DEPS_PREFIX:-}" "${CVC_BUILD_PREFIX:-}"; do
    if [[ -n "${_llvm}" && -f "${_llvm}/lib/cmake/clang/ClangConfig.cmake" ]]; then
        LLVM_INSTALL_DIR="${_llvm}"; break
    fi
done
if [[ -z "${LLVM_INSTALL_DIR:-}" ]]; then
    echo "shiboken6: llvm18 not found: no lib/cmake/clang/ClangConfig.cmake under" \
         "CVC_DEPS_PREFIX='${CVC_DEPS_PREFIX:-}' or CVC_BUILD_PREFIX='${CVC_BUILD_PREFIX:-}'." \
         "Install this recipe's dependency closure (cvcpkg install-deps)." >&2
    exit 1
fi
# The generator links libclang.so.18.1, and libclang cannot parse the Qt or
# libstdc++ headers without the resource headers (stddef.h, the intrinsics).
for _need in lib/libclang.so.18.1 lib/clang/18/include/stddef.h; do
    if [[ ! -e "${LLVM_INSTALL_DIR}/${_need}" ]]; then
        echo "shiboken6: ${LLVM_INSTALL_DIR}/${_need} is missing; the llvm18 bundle is incomplete" >&2
        exit 1
    fi
done
export LLVM_INSTALL_DIR
echo "shiboken6: LLVM_INSTALL_DIR=${LLVM_INSTALL_DIR} (cvcpkg llvm18)"
# The freshly built generator resolves libclang, libQt6Core and libshiboken
# from these dirs when it runs during this build.
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
    -DClang_DIR="${LLVM_INSTALL_DIR}/lib/cmake/clang" \
    -DLLVM_DIR="${LLVM_INSTALL_DIR}/lib/cmake/llvm" \
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
