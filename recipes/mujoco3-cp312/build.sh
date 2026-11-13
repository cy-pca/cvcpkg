#!/usr/bin/env bash
# recipes/mujoco3-cp312/build.sh — build the MuJoCo 3.17.0 Python bindings
# (PyPI `mujoco` 3.17.0, `import mujoco`) FROM SOURCE for CPython 3.12.
#
# A copy of recipes/mujoco-core-cp312/build.sh (MuJoCo 4) built against the
# mujoco3 recipe; see that file for the reasoning.  Differences that matter:
#   * Before 4.0 upstream shipped one PyPI distribution, `mujoco`; this is that
#     sdist.  Its setup.py lists Studio/Filament extensions too, but its CMake
#     builds them only when MUJOCO_PATH holds a Studio SDK (the
#     mujoco_studio_platform library), which the mujoco3 recipe does not build,
#     and setup.py skips missing extensions with a warning.  So the result is
#     the same module set as mujoco-core-cp312.
#   * `import mujoco` needs typing_extensions here (4.0 dropped it), a runtime
#     edge.  The other `Requires-Dist` entries (absl-py, etils, glfw, PyOpenGL,
#     websockets) are only imported by mujoco.viewer, mujoco.Renderer and other
#     optional submodules.
#
# How the sdist builds: setup.py runs CMake over mujoco/CMakeLists.txt against
# an already-built MuJoCo found through MUJOCO_PATH / MUJOCO_PLUGIN_PATH, and
# copies that libmujoco and its plugins INTO the wheel next to the extension
# modules (RPATH $ORIGIN / @loader_path; the .pyd directory on Windows).  The
# bindings' own CMake clones abseil, Eigen, pybind11, GLFW and lodepng; deps.sh
# feeds the same commits as sha256-checked archives through
# MUJOCO_CMAKE_DEP_CACHE, and a build that clones anyway fails.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
_CVC_ENV="${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM:-linux}.sh"
# shellcheck disable=SC1090
[ -f "${_CVC_ENV}" ] && . "${_CVC_ENV}"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/../_common/python-wheel.sh"   # cvc_python_exe, cvc_python_check
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/deps.sh"

: "${CVC_PYTHON_ABI:=cp312}"
: "${CVC_PYTHON_INTERPRETER:=python312}"
_NAME="mujoco3-${CVC_PYTHON_ABI}"
PY_EXE="$(cvc_python_exe)"
echo "${_NAME}: building with ${PY_EXE}"

_D="${CVC_PYTHON_ABI#cp}"; _D="${_D%t}"
_PYMM="${_D:0:1}.${_D:1}"                 # cp312 -> 3.12
_BP="${CVC_BUILD_PREFIX:-${CVC_DEPS_PREFIX}}"

# ── 1. Bridge the build-only backend (setuptools) onto sys.path ─────────────
export PYTHONPATH="${_BP}/lib/python${_PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"

# ── 2. Stage the C library from the `mujoco3` recipe ─────────────────────────
SDK="${CVC_BUILD_DIR}/mujoco-sdk"
rm -rf "${SDK}"
mkdir -p "${SDK}/lib" "${SDK}/include" "${SDK}/plugin"
[ -f "${_BP}/include/mujoco/mujoco.h" ] \
    || { echo "${_NAME}: ${_BP}/include/mujoco/mujoco.h not found (is the mujoco3 recipe staged?)" >&2; exit 1; }
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
# IPO off, as upstream's own wheel build does (.github/workflows/build_steps.sh
# passes -DCMAKE_INTERPROCEDURAL_OPTIMIZATION:BOOL=OFF through MUJOCO_CMAKE_ARGS):
# setup.py turns it on, and the LTO links of the big extension modules ran
# out of time or memory on the dev fleet (SIGTERM mid-link, then a pass on the
# rerun).  The modules wrap libmujoco, which keeps its own LTO.  Our -D comes
# after setup.py's on the CMake command line, so it wins.
_cmake_args="-GNinja -DMUJOCO_CMAKE_DEP_CACHE=${MJ_DEP_CACHE} -DCMAKE_INTERPROCEDURAL_OPTIMIZATION=OFF"
case "${CVC_PLATFORM:-linux}" in
    linux|freebsd|openbsd|netbsd)
        # X11 only (see recipes/mujoco/build.sh).
        _cmake_args="${_cmake_args} -DGLFW_BUILD_WAYLAND=OFF -DGLFW_BUILD_X11=ON"
        mj_add_gl_headers   # GL/gl.h for glfw3.h (see deps.sh); CMake reads CXXFLAGS
        # Cap parallel compiles by memory.  setup.py runs `cmake --build -j<ncpu>`,
        # and the four big binding sources (functions, specs, structs, enums)
        # then compile at once; on GitHub's 16 GB linux/arm64 runner the runner
        # shut down mid-compile twice (2026-11-10, linux-arm-build 42898701726).
        # A Ninja job pool caps concurrency whatever -j says: one compile per
        # 6 GB available, at least 1, at most the CPU count.
        _mem_kb="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null || true)"
        _ncpu="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
        _jobs=$(( ${_mem_kb:-0} / (6 * 1024 * 1024) ))
        [ "${_jobs}" -lt 1 ] && _jobs=1
        [ "${_jobs}" -gt "${_ncpu}" ] && _jobs="${_ncpu}"
        echo "${_NAME}: at most ${_jobs} parallel compiles (MemAvailable ${_mem_kb:-?} kB, ${_ncpu} CPUs)"
        _cmake_args="${_cmake_args} -DCMAKE_JOB_POOLS=mj_compile=${_jobs} -DCMAKE_JOB_POOL_COMPILE=mj_compile" ;;
    macos)
        _cmake_args="${_cmake_args} -DCMAKE_OSX_DEPLOYMENT_TARGET=${MACOSX_DEPLOYMENT_TARGET:-13.0}" ;;
esac
export MUJOCO_CMAKE="$(command -v cmake)"
export MUJOCO_CMAKE_ARGS="${_cmake_args}"

# ── 4. Build the wheel from the extracted sdist ─────────────────────────────
# -v: pip shows setup.py's CMake output, which the no-clone check reads.
WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
_log="${CVC_BUILD_DIR}/mujoco-build.log"
if ! "${PY_EXE}" -m pip wheel -v \
        --no-build-isolation \
        --no-deps \
        --no-index \
        --no-cache-dir \
        --wheel-dir "${WHEELHOUSE}" \
        "${CVC_SOURCE_DIR}" 2>&1 | tee "${_log}"; then
    mj_print_build_errors "${_log}"
    echo "${_NAME}: pip wheel failed" >&2
    exit 1
fi
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
