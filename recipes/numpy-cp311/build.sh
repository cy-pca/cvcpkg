#!/usr/bin/env bash
# recipes/numpy-cp311/build.sh — build NumPy 2.4.6 FROM SOURCE (meson-python)
# against cvcpkg's OpenBLAS (LP64), then install the resulting wheel into the
# python311 interpreter's site-packages.
#
# WHY FROM SOURCE (not the PyPI wheel): the published numpy wheel bundles its own
# auditwheel-vendored OpenBLAS (numpy.libs/libscipy_openblas-<hash>.so) and ships
# nothing for the BSDs. We compile so the extensions link cvcpkg's libopenblas.so.0
# by soname and resolve it, at runtime, out of the SAME activated prefix — no
# vendored copy — via an $ORIGIN-relative RUNPATH. Installed as a proper wheel
# (real dist-info) so pip's resolver treats numpy as satisfied and a user's later
# `pip install <other>` coexists.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"      # sets CVC_JOBS, toolchain
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"            # cvc_python_exe

: "${CVC_PYTHON_ABI:=cp311}"
PY="$(cvc_python_exe)"                         # <CVC_DEPS_PREFIX>/bin/python3.11
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
_D="${CVC_PYTHON_ABI#cp}"; _D="${_D%t}"; _PYMM="${_D:0:1}.${_D:1}"

# Bridge BUILD-only backends (meson-python, Cython, packaging, pyproject-metadata)
# from CVC_BUILD_PREFIX into the DEPS-prefix interpreter, and put the meson / ninja
# / pkg-config CLIs (native recipes, in the build prefix) first on PATH.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${_PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"

# ── Working `cython` for meson ──────────────────────────────────────────────
# numpy's meson build discovers Cython as a *compiler* by running the `cython`
# executable off PATH. The staged cython console script's shebang is a hardcoded
# absolute path to its own (ephemeral) build prefix's interpreter — unlike meson,
# whose recipe rewrites the shebang to `/usr/bin/env python3` — so the staged
# `cython` "cannot execute" and meson fails with
#   ERROR: Unknown compiler(s): [['cython'], ['cython3']]
# and numpy silently never builds from source. Shim a `cython` that runs the
# Cython module under THIS interpreter (module import works fine — only the
# script shebang is broken), and put it first on PATH.
CYTHON_SHIM="${CVC_BUILD_DIR}/cython-shim"
mkdir -p "${CYTHON_SHIM}"
cat > "${CYTHON_SHIM}/cython" <<EOF
#!/bin/sh
exec "${PY}" -m cython "\$@"
EOF
chmod +x "${CYTHON_SHIM}/cython"
export PATH="${CYTHON_SHIM}:${PATH}"

# ── Python headers for meson ────────────────────────────────────────────────
# meson's dependency('python') probes Python.h using sysconfig's INCLUDEPY,
# which in the staged interpreter is a stale (unrelocated) build-prefix path,
# so meson fails with "Cannot compile `Python.h`". sysconfig.get_path('include')
# IS relocated correctly (it derives from the running interpreter's prefix), so
# add it to the C search path as a fallback the stale -I can't override.
PYINC="$("${PY}" -c 'import sysconfig; print(sysconfig.get_path("include"))')"
if [ -n "${PYINC}" ] && [ -f "${PYINC}/Python.h" ]; then
  export CPATH="${PYINC}${CPATH:+:${CPATH}}"
fi

# ── BLAS selection ──────────────────────────────────────────────────────────
BLAS_ARGS=()
case "${CVC_PLATFORM}" in
  macos)
    # openblas has no macOS build (Accelerate is the platform BLAS).
    BLAS_ARGS+=( -C setup-args=-Dblas=accelerate -C setup-args=-Dlapack=accelerate ) ;;
  *)
    # Hermetic pkg-config: ONLY our prefix's openblas.pc visible (module name is
    # `openblas`, LP64, no symbol suffix). PKG_CONFIG_LIBDIR *replaces* the system
    # search path so no stray/system .pc leaks in.
    export PKG_CONFIG="${BLD}/bin/pkg-config"
    export PKG_CONFIG_PATH="${DEPS}/lib/pkgconfig"
    export PKG_CONFIG_LIBDIR="${DEPS}/lib/pkgconfig"
    BLAS_ARGS+=(
      -C setup-args=-Dblas=openblas
      -C setup-args=-Dlapack=openblas
      -C setup-args=-Dallow-noblas=false      # fail loud if openblas not found
      -C setup-args=-Duse-ilp64=false ) ;;    # our openblas is LP64
esac

WHEELOUT="${CVC_BUILD_DIR}/wheelhouse"; mkdir -p "${WHEELOUT}"

# ── Build from source (offline, no isolation) ───────────────────────────────
"${PY}" -m pip wheel \
  --no-build-isolation --no-deps --no-index --no-cache-dir \
  --wheel-dir "${WHEELOUT}" \
  "${BLAS_ARGS[@]}" \
  -C builddir="${CVC_BUILD_DIR}/meson" \
  -C compile-args=-j"${CVC_JOBS:-4}" \
  "${CVC_SOURCE_DIR}"

WHEEL="$(find "${WHEELOUT}" -maxdepth 1 -name 'numpy-*.whl' -print -quit)"
[ -n "${WHEEL}" ] || { echo "numpy-cp311: no wheel produced" >&2; exit 1; }
echo "numpy-cp311: built $(basename "${WHEEL}")"

# ── Install ONLY site-packages into the (empty) staging prefix ──────────────
# stage_bundle ships the ENTIRE CVC_INSTALL_DIR tree (package.files is not a
# filter), so installing --prefix into the empty per-recipe dir keeps it pure.
"${PY}" -m pip install --no-deps --no-index --no-compile \
  --prefix "${CVC_INSTALL_DIR}" "${WHEEL}"

NP_DIR="$(find "${CVC_INSTALL_DIR}" -maxdepth 4 -type d -name numpy -print -quit)"
[ -n "${NP_DIR}" ] || { echo "numpy-cp311: staged numpy/ not found" >&2; exit 1; }

# ── Relocatable RUNPATH per-file (meson strips the build rpath on install) ──
# numpy ships extensions at MIXED depths (numpy/*.so and numpy/_core/*.so,
# numpy/linalg/*.so, ...), so compute the $ORIGIN-relative path to <prefix>/lib
# per-file — never hard-code the depth. cvcpkg's own post-build pass then prepends
# a bare $ORIGIN and preserves these entries (ELF, shared, non-OpenBSD).
if [ "${CVC_PLATFORM}" != "macos" ]; then
  command -v patchelf >/dev/null 2>&1 || { echo "numpy-cp311: patchelf missing" >&2; exit 1; }
  while IFS= read -r -d '' so; do
    rel="$(realpath --relative-to="$(dirname "${so}")" "${CVC_INSTALL_DIR}/lib")"
    patchelf --set-rpath "\$ORIGIN:\$ORIGIN/${rel}" "${so}"
  done < <(find "${NP_DIR}" -name '*.so' -print0)
fi
command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── Verify: gemm works + prove NO vendored libs ─────────────────────────────
export PYTHONPATH="$(dirname "${NP_DIR}")${PYTHONPATH:+:${PYTHONPATH}}"
_LOADPATH="${DEPS}/lib${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib}"
if [ "${CVC_PLATFORM}" = "macos" ]; then
  export DYLD_LIBRARY_PATH="${_LOADPATH}${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
else
  export LD_LIBRARY_PATH="${_LOADPATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi
"${PY}" - <<'PYCHECK'
import os, numpy as np
a = np.arange(12, dtype=np.float64).reshape(3, 4)
assert a.sum() == 66.0, a.sum()
assert (a @ a.T).shape == (3, 3)            # exercises the BLAS gemm path
nd = os.path.dirname(np.__file__)
assert not any(d.endswith(".libs") for d in os.listdir(nd)), "vendored .libs present"
print("numpy", np.__version__, "from", np.__file__)
np.show_config()
print("numpy-cp311 build + verification complete")
PYCHECK
