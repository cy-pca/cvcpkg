#!/usr/bin/env bash
# recipes/mujoco-core-cp313/build.sh — build the MuJoCo Python bindings
# (PyPI `mujoco-core` 4.0.0, `import mujoco`) FROM SOURCE for CPython 3.13.
#
# Since 4.0 upstream splits its Python distribution: `mujoco-core` is the C
# library plus the bindings and depends only on numpy; `mujoco-studio` adds the
# Studio/Filament renderer; `mujoco` is a metapackage that installs both.
# This recipe is mujoco-core.  It provides the `mujoco` module: MjModel/
# MjData, the mj_* functions, mjSpec model editing, rollout and batch.
# mujoco.viewer and mujoco.Renderer also need upstream's optional `rendering`
# extra (the `glfw` and `PyOpenGL` PyPI packages), which cvcpkg does not
# package yet; the compiled side of the viewer (_simulate) is built and loads.
#
# How the sdist builds: setup.py runs CMake over mujoco/CMakeLists.txt against
# an already-built MuJoCo found through MUJOCO_PATH / MUJOCO_PLUGIN_PATH, and
# copies that libmujoco and its plugins INTO the wheel next to the extension
# modules (RPATH $ORIGIN / @loader_path; the .pyd directory on Windows).  So:
#   * the C library comes from the `mujoco` recipe, a BUILD dependency; it is
#     staged into a small SDK directory first, because setup.py walks the
#     whole of MUJOCO_PATH and would otherwise scan the entire build prefix;
#   * the installed package carries its own libmujoco, exactly like the PyPI
#     wheel, so it needs nothing from the `mujoco` recipe at run time.
# The bindings' own CMake clones abseil, Eigen, pybind11 3.1, GLFW and lodepng;
# deps.sh feeds the same commits as sha256-checked archives through
# MUJOCO_CMAKE_DEP_CACHE, and a build that clones anyway fails.
#
# --no-build-isolation / --no-index / --no-deps, as in every cpXYZ recipe: the
# PEP 517 backend (setuptools) is a depends.build edge bridged onto sys.path.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
_CVC_ENV="${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM:-linux}.sh"
# shellcheck disable=SC1090
[ -f "${_CVC_ENV}" ] && . "${_CVC_ENV}"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/../_common/python-wheel.sh"   # cvc_python_exe, cvc_python_check
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/deps.sh"

: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
_NAME="mujoco-core-${CVC_PYTHON_ABI}"
PY_EXE="$(cvc_python_exe)"
echo "${_NAME}: building with ${PY_EXE}"

_D="${CVC_PYTHON_ABI#cp}"; _D="${_D%t}"
_PYMM="${_D:0:1}.${_D:1}"                 # cp313 -> 3.13
_BP="${CVC_BUILD_PREFIX:-${CVC_DEPS_PREFIX}}"

# ── 1. Bridge the build-only backend (setuptools) onto sys.path ─────────────
export PYTHONPATH="${_BP}/lib/python${_PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"

# ── 2. Stage the C library from the `mujoco` recipe ─────────────────────────
SDK="${CVC_BUILD_DIR}/mujoco-sdk"
rm -rf "${SDK}"
mkdir -p "${SDK}/lib" "${SDK}/include" "${SDK}/plugin"
[ -f "${_BP}/include/mujoco/mujoco.h" ] \
    || { echo "${_NAME}: ${_BP}/include/mujoco/mujoco.h not found (is the mujoco recipe staged?)" >&2; exit 1; }
cp -R "${_BP}/include/mujoco" "${SDK}/include/"
case "${CVC_PLATFORM:-linux}" in
    macos) cp -P "${_BP}"/lib/libmujoco*.dylib "${SDK}/lib/" ;;
    *)     cp -P "${_BP}"/lib/libmujoco.so* "${SDK}/lib/" ;;
esac
cp "${_BP}"/bin/mujoco_plugin/* "${SDK}/plugin/"
export MUJOCO_PATH="${SDK}"
export MUJOCO_PLUGIN_PATH="${SDK}/plugin"

# ── 3. Pinned sources for the bindings' CMake ───────────────────────────────
mj_fetch_deps
_cmake_args="-GNinja -DMUJOCO_CMAKE_DEP_CACHE=${MJ_DEP_CACHE}"
case "${CVC_PLATFORM:-linux}" in
    linux|freebsd|openbsd|netbsd)
        # X11 only (see recipes/mujoco/build.sh).
        _cmake_args="${_cmake_args} -DGLFW_BUILD_WAYLAND=OFF -DGLFW_BUILD_X11=ON"
        mj_add_gl_headers ;;   # GL/gl.h for glfw3.h (see deps.sh); CMake reads CXXFLAGS
    macos)
        _cmake_args="${_cmake_args} -DCMAKE_OSX_DEPLOYMENT_TARGET=${MACOSX_DEPLOYMENT_TARGET:-13.0}" ;;
esac
export MUJOCO_CMAKE="$(command -v cmake)"
export MUJOCO_CMAKE_ARGS="${_cmake_args}"

# ── 4. Build the wheel from the extracted sdist ─────────────────────────────
# -v: pip shows setup.py's CMake output, which the no-clone check reads.
WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
_log="${CVC_BUILD_DIR}/mujoco-core-build.log"
"${PY_EXE}" -m pip wheel -v \
    --no-build-isolation \
    --no-deps \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}" 2>&1 | tee "${_log}"
mj_check_no_git_fetch "${_log}"

WHEEL=""
for WHEEL in "${WHEELHOUSE}"/*.whl; do [ -e "${WHEEL}" ] && break; WHEEL=""; done
[ -n "${WHEEL}" ] || { echo "${_NAME}: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "${_NAME}: built $(basename "${WHEEL}")"

# ── 5. Install it into this recipe's (empty) staging prefix ─────────────────
"${PY_EXE}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

mj_install_licenses "${CVC_INSTALL_DIR}/share/licenses/${_NAME}"

# ── 6. Verify against the RUNTIME closure only ──────────────────────────────
unset PYTHONPATH
cvc_python_check "$(cat "${SCRIPT_DIR}/check.py")"
