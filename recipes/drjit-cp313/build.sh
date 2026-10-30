#!/usr/bin/env bash
# recipes/drjit-cp313/build.sh -- build Dr.Jit 1.5.0 FROM SOURCE for the cp313
# interpreter column (scikit-build-core + nanobind 2.14.0), then install the
# wheel into this recipe's staging prefix.
#
# WHY FROM SOURCE: PyPI ships drjit as wheels only.  The tree is drjit-src (the
# v1.5.0 commit archive plus its submodules).  Under scikit-build drjit's CMake
# runs `python -m nanobind --cmake_dir` and builds against the nanobind-cp313
# column, so nanobind 2.14.0 -- the exact [build-system] pin -- must be on the
# build interpreter's path.
#
# Nothing here links a cvcpkg library: the LLVM and CUDA backends dlopen()
# their libraries at run time (DRJIT_DYNAMIC_LLVM), and Metal is a system
# framework.  libLLVM is found at run time through
# drjit-libllvm-from-prefix.patch; test.sh proves it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # toolchain, CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"           # cvc_python_exe, ...

# ── 1. Resolve the prefix interpreter for this column ───────────────────────
: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "drjit-cp313: building with ${PY} (python${PYMM})"

# On x86_64 Dr.Jit is compiled for Haswell (x86-64-v3: AVX2, FMA, F16C), as
# upstream's wheels are, and the build imports the module it just built
# (nanobind's stubgen).  On an older host that dies with SIGILL mid-build;
# stop here and say why instead.
case "$(uname -m)" in
    x86_64|amd64)
        if [ -r /proc/cpuinfo ]; then
            missing=""
            for f in avx2 fma f16c; do grep -q -w "${f}" /proc/cpuinfo || missing="${missing} ${f}"; done
            if [ -n "${missing}" ]; then
                echo "drjit-cp313: this x86_64 build host lacks${missing}; Dr.Jit needs a Haswell or newer CPU (x86-64-v3) to build and run" >&2
                exit 1
            fi
        fi ;;
esac

# ── 2. Bridge the build-only Python packages onto that interpreter ──────────
# depends.build columns land in CVC_BUILD_PREFIX, which the DEPS-prefix
# interpreter does not import; --no-build-isolation needs them on sys.path.
# cmake and ninja (native recipes) come first on PATH.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" - <<'PYCHECK'
import nanobind, scikit_build_core, hatch_fancy_pypi_readme, typing_extensions
assert nanobind.__version__ == "2.14.0", "drjit 1.5.0 pins nanobind==2.14.0, found " + nanobind.__version__
print("drjit-cp313: scikit-build-core", scikit_build_core.__version__, "| nanobind", nanobind.__version__,
      "at", nanobind.cmake_dir())
PYCHECK

# ── 3. scikit-build-core settings ──────────────────────────────────────────
# Our cmake/ninja (PATH), one build tree outside the source copy, and no test
# suite.  CMAKE_PREFIX_PATH comes from env-${CVC_PLATFORM}.sh.
export CMAKE_GENERATOR=Ninja
export CMAKE_BUILD_PARALLEL_LEVEL="${CVC_JOBS}"
# The source copy has no .git; keep git from answering for an enclosing
# checkout (a CI workspace) if anything asks.
export GIT_CEILING_DIRECTORIES="$(dirname "${CVC_SOURCE_DIR}")"

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
    -C cmake.define.DRJIT_ENABLE_TESTS=OFF \
    "${CVC_SOURCE_DIR}"

shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/drjit-*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "drjit-cp313: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "drjit-cp313: built $(basename "${WHEEL}")"
case "$(basename "${WHEEL}")" in
    *-cp313-cp313-*) ;;
    *) echo "drjit-cp313: expected a cp313-cp313 wheel, got $(basename "${WHEEL}")" >&2; exit 1 ;;
esac

# ── 4. Install ONLY this package into the (empty) staging prefix ────────────
# stage_bundle ships the whole CVC_INSTALL_DIR, so installing --prefix into the
# initially empty per-recipe dir is what keeps the bundle to just drjit.
"${PY}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

SITE_PACKAGES="${CVC_INSTALL_DIR}/lib/python${PYMM}/site-packages"
[ -f "${SITE_PACKAGES}/drjit/__init__.py" ] || { echo "drjit-cp313: drjit/ not staged under ${SITE_PACKAGES}" >&2; exit 1; }
grep -q '_cvcpkg_default_libllvm' "${SITE_PACKAGES}/drjit/__init__.py" \
    || { echo "drjit-cp313: drjit-libllvm-from-prefix.patch is not in the installed drjit/__init__.py" >&2; exit 1; }
[ -f "${SITE_PACKAGES}/drjit/cmake/drjit/drjitConfig.cmake" ] \
    || { echo "drjit-cp313: drjit/cmake/drjit/drjitConfig.cmake missing (mitsuba-cp313 builds against it)" >&2; exit 1; }

command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true
echo "drjit-cp313: build complete (test.sh verifies)"
