#!/usr/bin/env bash
# recipes/mitsuba-cp313/build.sh -- build Mitsuba 3.9.1 FROM SOURCE for the
# cp313 interpreter column (scikit-build-core + nanobind 2.14.0, against the
# installed drjit-cp313 1.5.0), then install the wheel into this recipe's
# staging prefix.
#
# WHY FROM SOURCE: PyPI ships mitsuba as wheels only.  The tree is mitsuba-src
# (the v3.9.1 commit archive plus its submodules).  Under scikit-build
# Mitsuba's CMake find_package()s the INSTALLED drjit (drjit.get_cmake_dir())
# and nanobind (`python -m nanobind --cmake_dir`), and compiles its own forks of
# Embree, OpenEXR, libpng and libjpeg-turbo into -mitsuba-suffixed libraries
# inside the package.  zlib is the one system library those forks look for;
# it is pinned to cvcpkg's (ZLIB_ROOT) so a host libz cannot be picked up.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # toolchain, CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"           # cvc_python_exe, ...
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/variants.sh"                          # mi_variants

# ── 1. Resolve the prefix interpreter for this column ───────────────────────
: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
VARIANTS="$(mi_variants)"
echo "mitsuba-cp313: building with ${PY} (python${PYMM}); variants ${VARIANTS}"

# ── 2. Bridge the build-only Python packages onto that interpreter ──────────
# cmake, ninja and nasm (native recipes) come first on PATH; the backend and
# nanobind columns live in the build prefix's site-packages.  drjit-cp313 is a
# runtime dep, in the DEPS prefix the interpreter already imports.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
DRJIT_CMAKE_DIR="$("${PY}" - <<'PYCHECK'
import drjit, nanobind, scikit_build_core, hatch_fancy_pypi_readme, typing_extensions
assert drjit.__version__ == "1.5.0", "mitsuba 3.9.1 pins drjit==1.5.0, found " + drjit.__version__
assert nanobind.__version__ == "2.14.0", "mitsuba 3.9.1 pins nanobind==2.14.0, found " + nanobind.__version__
print(drjit.get_cmake_dir())
PYCHECK
)"
[ -f "${DRJIT_CMAKE_DIR}/drjitConfig.cmake" ] \
    || { echo "mitsuba-cp313: no drjitConfig.cmake in ${DRJIT_CMAKE_DIR}" >&2; exit 1; }
echo "mitsuba-cp313: drjit CMake package at ${DRJIT_CMAKE_DIR}"

# ── 3. scikit-build-core / CMake settings ──────────────────────────────────
export CMAKE_GENERATOR=Ninja
export CMAKE_BUILD_PARALLEL_LEVEL="${CVC_JOBS}"
# CMakeLists.txt runs `git rev-parse` / `git log` for the version banner; the
# source copy has no .git, so keep git from answering for an enclosing
# checkout (a CI workspace) instead.
export GIT_CEILING_DIRECTORIES="$(dirname "${CVC_SOURCE_DIR}")"

ZLIB_ARGS=()
case "${CVC_PLATFORM}" in
    windows) ;;   # ext/zlib is built in-tree there
    *)
        ls "${DEPS}"/lib/libz.* >/dev/null 2>&1 \
            || { echo "mitsuba-cp313: no libz under ${DEPS}/lib -- is the zlib runtime dep installed?" >&2; exit 1; }
        ZLIB_ARGS+=( -C "cmake.define.ZLIB_ROOT=${DEPS}" ) ;;
esac
NASM_ARGS=()
if [ -x "${BLD}/bin/nasm" ]; then
    NASM_ARGS+=( -C "cmake.define.CMAKE_ASM_NASM_COMPILER=${BLD}/bin/nasm" )
fi

WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY}" -m pip wheel \
    --no-deps \
    --no-build-isolation \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    -C build-dir="${CVC_BUILD_DIR}/skbuild" \
    -C cmake.build-type=Release \
    -C "cmake.define.MI_DEFAULT_VARIANTS=${VARIANTS}" \
    -C "cmake.define.MI_DRJIT_CMAKE_DIR=${DRJIT_CMAKE_DIR}" \
    -C cmake.define.MI_ENABLE_EMBREE=ON \
    ${ZLIB_ARGS[@]+"${ZLIB_ARGS[@]}"} \
    ${NASM_ARGS[@]+"${NASM_ARGS[@]}"} \
    "${CVC_SOURCE_DIR}"

shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/[Mm]itsuba-*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "mitsuba-cp313: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "mitsuba-cp313: built $(basename "${WHEEL}")"
case "$(basename "${WHEEL}")" in
    *-cp313-cp313-*) ;;
    *) echo "mitsuba-cp313: expected a cp313-cp313 wheel, got $(basename "${WHEEL}")" >&2; exit 1 ;;
esac

# ── 4. Install ONLY this package into the (empty) staging prefix ────────────
"${PY}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

SITE_PACKAGES="${CVC_INSTALL_DIR}/lib/python${PYMM}/site-packages"
[ -f "${SITE_PACKAGES}/mitsuba/__init__.py" ] || { echo "mitsuba-cp313: mitsuba/ not staged under ${SITE_PACKAGES}" >&2; exit 1; }
for d in data/ior data/sunsky; do
    [ -d "${SITE_PACKAGES}/mitsuba/${d}" ] || { echo "mitsuba-cp313: mitsuba/${d} missing from the wheel" >&2; exit 1; }
done
# The compiled variant list is baked into mitsuba/config.py.
for v in $(printf '%s' "${VARIANTS}" | tr ',' ' '); do
    grep -q "'${v}'\|\"${v}\"" "${SITE_PACKAGES}/mitsuba/config.py" \
        || { echo "mitsuba-cp313: variant ${v} missing from mitsuba/config.py" >&2; cat "${SITE_PACKAGES}/mitsuba/config.py" >&2; exit 1; }
done

command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true
echo "mitsuba-cp313: build complete (test.sh verifies)"
