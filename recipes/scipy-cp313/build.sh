#!/usr/bin/env bash
# recipes/scipy-cp313/build.sh — build SciPy FROM SOURCE (meson-python) for the
# cp313 interpreter column, against cvcpkg's OpenBLAS, with cvcpkg's gfortran.
#
# The Linux counterpart of scipy-cp312/build.ps1 (MinGW-w64), and shaped like
# numpy-cp313/build.sh: --no-build-isolation with the backends bridged in from
# the build prefix, a hermetic pkg-config that sees only the prefix, the wheel
# installed into an empty staging dir, then a per-file $ORIGIN RUNPATH so the
# extensions find libopenblas.so.0 and libgfortran.so.5 in the consumer's
# merged prefix. Nothing vendored ships: there is no scipy.libs/.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"      # CVC_JOBS, toolchain
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"            # cvc_python_exe, cvc_python_check

# ── 1. Resolve the prefix interpreter for this column ───────────────────────
: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "scipy-cp313: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# meson-python, Cython, pybind11 and Pythran are depends.build columns, staged
# into CVC_BUILD_PREFIX; the interpreter in CVC_DEPS_PREFIX does not import from
# there on its own. bin/ goes first on PATH too: meson runs `cython` and
# `pythran` as programs.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"

# Python headers: meson's dependency('python') takes INCLUDEPY from sysconfig,
# which in the staged interpreter is a stale (unrelocated) build-prefix path, so
# every extension fails with "Python.h: No such file or directory".
# sysconfig.get_path('include') IS relocated (it derives from the running
# interpreter's prefix); put it on the C search path, as numpy-cp313 does.
PYINC="$("${PY}" -c 'import sysconfig; print(sysconfig.get_path("include"))')"
[ -f "${PYINC}/Python.h" ] || { echo "scipy-cp313: no Python.h under ${PYINC}" >&2; exit 1; }
export CPATH="${PYINC}${CPATH:+:${CPATH}}"

# ── 3. The Fortran compiler: cvcpkg's gfortran, never the builder's ─────────
# SciPy still compiles Fortran (ARPACK, PROPACK, the FITPACK/ODEPACK leftovers),
# so it needs a gfortran; an ambient /usr/bin/gfortran deciding how SciPy's
# Fortran is built is the non-hermeticity this recipe set exists to prevent.
# The gfortran package (a build dependency) is GCC 11.4.0, the GCC the linux
# fleet compiles C/C++ with, so the objects mix; its runtime libraries reach
# consumers through the gcc-runtime package.
FC=""
for _fc in "${BLD}/bin/gfortran" "${DEPS}/bin/gfortran"; do
    if [ -x "${_fc}" ]; then FC="${_fc}"; break; fi
done
[ -n "${FC}" ] || {
    echo "scipy-cp313: no gfortran in ${BLD}/bin or ${DEPS}/bin — is the 'gfortran' build dep in the closure?" >&2
    exit 1
}
"${FC}" --version | grep -q "cvcpkg gfortran" || {
    echo "scipy-cp313: ${FC} is not cvcpkg's gfortran:" >&2
    "${FC}" --version >&2
    exit 1
}
export FC
echo "scipy-cp313: gfortran $("${FC}" -dumpversion) from ${FC}"

# ── 4. Hermetic pkg-config: OUR openblas, pybind11 from its package ─────────
for _pc in "${BLD}/bin/pkg-config" "${DEPS}/bin/pkg-config" "$(command -v pkg-config 2>/dev/null || true)"; do
    if [ -x "${_pc}" ]; then export PKG_CONFIG="${_pc}"; break; fi
done
[ -n "${PKG_CONFIG:-}" ] || { echo "scipy-cp313: no pkg-config in ${BLD}/bin, ${DEPS}/bin or PATH" >&2; exit 1; }
# pybind11 keeps its .pc inside the python package (pybind11/share/pkgconfig),
# not in <prefix>/lib/pkgconfig. PKG_CONFIG_LIBDIR REPLACES the default search
# path, so that directory has to be named or meson fails with
#   Dependency "pybind11" not found (tried pkg-config, config-tool and cmake)
PYBIND_SHARE="$("${PY}" -c 'import os, pybind11; print(os.path.join(os.path.dirname(pybind11.__file__), "share"))')"
export PKG_CONFIG_PATH="${DEPS}/lib/pkgconfig:${BLD}/lib/pkgconfig:${PYBIND_SHARE}/pkgconfig"
export PKG_CONFIG_LIBDIR="${PKG_CONFIG_PATH}"
export CMAKE_PREFIX_PATH="${PYBIND_SHARE}${CMAKE_PREFIX_PATH:+:${CMAKE_PREFIX_PATH}}"
# The header dir goes on CXXFLAGS too, unconditionally (as matplotlib and
# contourpy do).  The fleet builder rewrites every .pc file's prefix= to the
# deps prefix root (builder._rewrite_pc_prefixes), which turns pybind11.pc's
# relocatable prefix=${pcfiledir}/../../ into <prefix>: meson still finds
# pybind11 2.13.6, but its include dir is <prefix>/include and pypocketfft fails
# "pybind11/pybind11.h: No such file or directory" (all four scipy columns on
# 2026-10-22, jobs 22720-22723; a local build never runs that rewrite).
PYBIND_INC="$("${PY}" -c 'import pybind11; print(pybind11.get_include())')"
[ -f "${PYBIND_INC}/pybind11/pybind11.h" ] || {
    echo "scipy-cp313: no pybind11/pybind11.h under ${PYBIND_INC}" >&2
    exit 1
}
export CXXFLAGS="-I${PYBIND_INC} ${CXXFLAGS:-}"
"${PKG_CONFIG}" --exists openblas || {
    echo "scipy-cp313: openblas.pc not found by ${PKG_CONFIG} (PKG_CONFIG_LIBDIR=${PKG_CONFIG_LIBDIR})" >&2
    exit 1
}
echo "scipy-cp313: openblas $("${PKG_CONFIG}" --modversion openblas) via ${PKG_CONFIG}"

# ── 5. Parallelism, bounded by memory ──────────────────────────────────────
# SciPy's heavy C++ targets (HiGHS, SuperLU, the _ufuncs units, Pythran
# output) take well over 1 GB of compiler per job; at -j<cores> on a box with
# less than ~3 GB per core the build dies in the OOM killer, which reads as a
# random compiler crash. CVC_JOBS, when set, still wins.
JOBS="${CVC_JOBS:-}"
if [ -z "${JOBS}" ]; then
    _cores="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
    _mem_gb="$(awk '/^MemTotal:/ {print int($2 / 1048576)}' /proc/meminfo 2>/dev/null || echo 8)"
    _by_ram=$(( _mem_gb / 3 )); [ "${_by_ram}" -ge 2 ] || _by_ram=2
    JOBS=$(( _cores < _by_ram ? _cores : _by_ram ))
fi
echo "scipy-cp313: building with -j${JOBS}"

# ── 6. Build the wheel (offline, no isolation) ─────────────────────────────
# --wrap-mode=nofallback: a dependency meson cannot find fails the build
# instead of falling back to a download. SciPy's own boost.math and qhull are
# in-tree subprojects (use-system-libraries=none, the default), not fallbacks.
WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
if ! "${PY}" -m pip wheel \
    --no-build-isolation --no-deps --no-index --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    -C setup-args=--wrap-mode=nofallback \
    -C setup-args=-Dblas=openblas \
    -C setup-args=-Dlapack=openblas \
    -C setup-args=-Duse-pythran=true \
    -C builddir="${CVC_BUILD_DIR}/meson" \
    -C compile-args=-j"${JOBS}" \
    "${CVC_SOURCE_DIR}"; then
    _log="${CVC_BUILD_DIR}/meson/meson-logs/meson-log.txt"
    echo "----- meson-log.txt (${_log}) -----" >&2
    if [ -f "${_log}" ]; then tail -n 200 "${_log}" >&2; else echo "(no meson log)" >&2; fi
    exit 1
fi

shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/scipy-*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "scipy-cp313: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "scipy-cp313: built $(basename "${WHEEL}")"

# ── 7. Install into THIS recipe's staging prefix ──────────────────────────
"${PY}" -m pip install --no-deps --no-index --no-compile --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" "${WHEEL}"
SITE_PACKAGES="${CVC_INSTALL_DIR}/lib/python${PYMM}/site-packages"
[ -d "${SITE_PACKAGES}/scipy" ] || { echo "scipy-cp313: staged scipy/ not found under ${SITE_PACKAGES}" >&2; exit 1; }

# ── 8. Make the extensions relocatable ─────────────────────────────────────
# Every extension gets $ORIGIN plus the path from its own directory to
# <prefix>/lib, where libopenblas.so.0 and libgfortran.so.5 live at the
# consumer. Depths differ across scipy's subpackages, so compute it per file.
command -v patchelf >/dev/null 2>&1 || { echo "scipy-cp313: patchelf missing" >&2; exit 1; }
_n=0
while IFS= read -r -d '' _so; do
    _rel="$("${PY}" -c 'import os, sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' \
        "${CVC_INSTALL_DIR}/lib" "$(dirname "${_so}")")"
    patchelf --set-rpath "\$ORIGIN:\$ORIGIN/${_rel}" "${_so}"
    _n=$((_n + 1))
done < <(find "${SITE_PACKAGES}/scipy" -name '*.so' -print0)
echo "scipy-cp313: stamped a relocatable RUNPATH on ${_n} extension(s)"

command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── 9. Verify: LAPACK, Fortran code, the parts jax uses, nothing vendored ──
# Against the RUNTIME closure only. The staged RUNPATH points at <prefix>/lib,
# which at build time is ${DEPS}/lib rather than this staging dir, so widen the
# loader path for the check alone.
unset PYTHONPATH
export LD_LIBRARY_PATH="${DEPS}/lib${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
cvc_python_check "$(cat <<'PYCHECK'
import os
import numpy as np
import scipy
from scipy import integrate, linalg, ndimage, sparse, special
from scipy.sparse.linalg import eigsh

a = np.array([[3.0, 1.0], [1.0, 2.0]])
x = linalg.solve(a, np.array([9.0, 8.0]))           # LAPACK gesv
assert np.allclose(a @ x, [9.0, 8.0]), x
assert np.all(linalg.eigvalsh(a) > 0)                # LAPACK syevd
q, r = linalg.qr(a)                                  # LAPACK geqrf
assert np.allclose(q @ r, a)
assert np.allclose(linalg.expm(np.zeros((2, 2))), np.eye(2))

# ARPACK is Fortran: this is what proves gfortran output links and runs.
m = sparse.diags([1.0, 2.0, 3.0, 4.0, 5.0])
w = eigsh(m, k=2, which="LA", return_eigenvectors=False)
assert np.allclose(sorted(w), [4.0, 5.0]), w

# jax.scipy leans on special and the ODE solvers back its reference tests.
assert np.isclose(special.gammaln(5.0), np.log(24.0))
sol = integrate.solve_ivp(lambda t, y: -y, (0.0, 1.0), [1.0], rtol=1e-8, atol=1e-10)
assert np.isclose(sol.y[0, -1], np.exp(-1.0), rtol=1e-6), sol.y[0, -1]
d = ndimage.distance_transform_edt(np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]]))
assert np.isclose(d[1, 1], 0.0) and np.isclose(d[0, 0], np.sqrt(2.0)), d

sd = os.path.dirname(scipy.__file__)
vendored = [p for p in os.listdir(os.path.dirname(sd)) if p.startswith("scipy") and p.endswith(".libs")]
assert not vendored, "vendored libs present: %s" % vendored
print("scipy", scipy.__version__, "from", scipy.__file__)
PYCHECK
)"

echo "scipy-cp313: build + verification complete"
