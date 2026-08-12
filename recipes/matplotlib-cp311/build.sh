#!/usr/bin/env bash
# recipes/matplotlib-cp311/build.sh — build matplotlib 3.10.0 FROM SOURCE
# against cvcpkg's freetype and qhull (hand-converted).
#
# WHY HAND-WRITTEN: three things the generator cannot infer.
#  1. meson's dependency('pybind11') misses the staged copy (the shipped
#     pybind11-config script has a dead ephemeral-prefix shebang) — same fix
#     as recipes/contourpy-cp311: the .pc dir via --pkgconfigdir plus the
#     header dir unconditionally via CXXFLAGS.
#  2. matplotlib's default build DOWNLOADS freetype 2.6.1 and qhull 8.0.2
#     through meson wrap files — impossible on an offline builder and
#     non-hermetic anywhere else.  -Dsystem-freetype/-Dsystem-qhull link
#     cvcpkg's own recipes instead (qhull_r.pc ships in the qhull recipe).
#  3. The extensions link prefix libraries, so they need the numpy-style
#     per-file $ORIGIN RUNPATH pass and a check that proves the render path,
#     not just an import.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"      # toolchain, CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"            # cvc_python_exe

: "${CVC_PYTHON_ABI:=cp311}"
: "${CVC_PYTHON_INTERPRETER:=python311}"
PY_EXE="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
echo "matplotlib-cp311: building with ${PY_EXE}"

# ── Python headers ──────────────────────────────────────────────────────────
# meson's dependency('python') probes Python.h via sysconfig's INCLUDEPY,
# which in the staged interpreter can be a stale (unrelocated) build-prefix
# path — numpy's build hit and documented the same thing.  get_path('include')
# derives from the running prefix and is correct; add it as a fallback the
# stale -I cannot override.
PYINC="$("${PY_EXE}" -c 'import sysconfig; print(sysconfig.get_path("include"))')"
if [ -n "${PYINC}" ] && [ -f "${PYINC}/Python.h" ]; then
  export CPATH="${PYINC}${CPATH:+:${CPATH}}"
fi

# ── Bridge the build-only backends onto the interpreter's path ──────────────
_D="${CVC_PYTHON_ABI#cp}"; _D="${_D%t}"
_PYMM="${_D:0:1}.${_D:1}"                 # cp311 -> X.Y
if [ -n "${CVC_BUILD_PREFIX:-}" ]; then
    _BP_SITE="${CVC_BUILD_PREFIX}/lib/python${_PYMM}/site-packages"
    export PYTHONPATH="${_BP_SITE}${PYTHONPATH:+:${PYTHONPATH}}"
fi

# ── pybind11 discovery (contourpy's fleet-proven pair of routes) ────────────
_PB11_PC="$("${PY_EXE}" -m pybind11 --pkgconfigdir)"
[ -n "${_PB11_PC}" ] || { echo "matplotlib-cp311: python -m pybind11 --pkgconfigdir returned nothing" >&2; exit 1; }
_PB11_INC="$("${PY_EXE}" -c 'import pybind11; print(pybind11.get_include())')"
[ -d "${_PB11_INC}" ] || { echo "matplotlib-cp311: pybind11.get_include() -> ${_PB11_INC} does not exist" >&2; exit 1; }
export CXXFLAGS="-I${_PB11_INC} ${CXXFLAGS:-}"

# ── Version detection + tool resolution ─────────────────────────────────────
# meson.build:4 shells out to `python3 -m setuptools_scm` by PATH lookup —
# NOT through meson-python's native-file interpreter — so an unshimmed PATH
# hands it the builder's system python (no setuptools_scm; numpy's cython
# shim exists for the same class of breakage).  Pin `python3` to THIS
# column's interpreter, put the prefixes first for every other tool, and pin
# the version outright: an sdist build has no git metadata to derive it from.
_SHIM="${CVC_BUILD_DIR:-${CVC_SOURCE_DIR}}/pyshim"
mkdir -p "${_SHIM}"
printf '#!/bin/sh\nexec "%s" "$@"\n' "${PY_EXE}" > "${_SHIM}/python3"
chmod +x "${_SHIM}/python3"
export PATH="${_SHIM}:${BLD}/bin:${DEPS}/bin:${PATH}"
export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_MATPLOTLIB="3.10.0"
export SETUPTOOLS_SCM_PRETEND_VERSION="3.10.0"

# ── Hermetic native-library discovery (freetype, qhull) ─────────────────────
for _pc in "${BLD}/bin/pkg-config" "${DEPS}/bin/pkg-config" "$(command -v pkg-config 2>/dev/null || true)"; do
    if [ -x "${_pc}" ]; then export PKG_CONFIG="${_pc}"; break; fi
done
[ -n "${PKG_CONFIG:-}" ] || { echo "matplotlib-cp311: no pkg-config in ${BLD}/bin, ${DEPS}/bin or PATH" >&2; exit 1; }
export PKG_CONFIG_PATH="${_PB11_PC}:${DEPS}/lib/pkgconfig:${BLD}/lib/pkgconfig"
export PKG_CONFIG_LIBDIR="${_PB11_PC}:${DEPS}/lib/pkgconfig:${BLD}/lib/pkgconfig"
for _mod in freetype2 qhull_r; do
    if ! "${PKG_CONFIG}" --exists "${_mod}"; then
        echo "matplotlib-cp311: ${_mod}.pc not found by ${PKG_CONFIG}" >&2
        echo "  PKG_CONFIG_LIBDIR=${PKG_CONFIG_LIBDIR}" >&2
        ls -la "${DEPS}/lib/pkgconfig" 2>&1 | head -40 >&2
        exit 1
    fi
done
export CFLAGS="-I${DEPS}/include -I${DEPS}/include/freetype2 ${CFLAGS:-}"
export CXXFLAGS="-I${DEPS}/include -I${DEPS}/include/freetype2 ${CXXFLAGS}"
export LDFLAGS="-L${DEPS}/lib ${LDFLAGS:-}"
echo "matplotlib-cp311: pybind11 pc=${_PB11_PC} include=${_PB11_INC}; freetype $("${PKG_CONFIG}" --modversion freetype2), qhull_r $("${PKG_CONFIG}" --modversion qhull_r)"

WHEELHOUSE="${CVC_BUILD_DIR:-${CVC_SOURCE_DIR}}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
# -Dsystem-freetype/-Dsystem-qhull: link the prefix libraries instead of
# meson-wrap DOWNLOADS (offline builders cannot fetch; nothing hermetic may).
"${PY_EXE}" -m pip wheel \
    --no-build-isolation \
    --no-deps \
    --no-index \
    --no-cache-dir \
    -C setup-args=-Dsystem-freetype=true \
    -C setup-args=-Dsystem-qhull=true \
    -C compile-args=-j"${CVC_JOBS:-4}" \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

WHEEL="$(find "${WHEELHOUSE}" -maxdepth 1 -name 'matplotlib-*.whl' -print -quit)"
[ -n "${WHEEL}" ] || { echo "matplotlib-cp311: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "matplotlib-cp311: built $(basename "${WHEEL}")"

"${PY_EXE}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

MPL_DIR="$(find "${CVC_INSTALL_DIR}" -maxdepth 4 -type d -name matplotlib -print -quit)"
[ -n "${MPL_DIR}" ] || { echo "matplotlib-cp311: staged matplotlib/ not found" >&2; exit 1; }

# ── Relocatable RUNPATH per-file (extensions at MIXED depths) ───────────────
if [ "${CVC_PLATFORM}" != "macos" ]; then
  command -v patchelf >/dev/null 2>&1 || { echo "matplotlib-cp311: patchelf missing" >&2; exit 1; }
  while IFS= read -r -d '' so; do
    rel="$("${PY_EXE}" -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "${CVC_INSTALL_DIR}/lib" "$(dirname "${so}")")"
    patchelf --set-rpath "\$ORIGIN:\$ORIGIN/${rel}" "${so}"
  done < <(find "$(dirname "${MPL_DIR}")" -name '*.so' -path '*matplotlib*' -print0; find "$(dirname "${MPL_DIR}")/mpl_toolkits" -name '*.so' -print0 2>/dev/null)
fi
command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── Verify: the RENDER path works, not just the import ──────────────────────
unset PYTHONPATH
export PYTHONPATH="$(dirname "${MPL_DIR}")"
_LOADPATH="${DEPS}/lib${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib}"
if [ "${CVC_PLATFORM}" = "macos" ]; then
  export DYLD_LIBRARY_PATH="${_LOADPATH}${DYLD_LIBRARY_PATH:+:${DYLD_LIBRARY_PATH}}"
else
  export LD_LIBRARY_PATH="${_LOADPATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"
fi
# The runtime closure (numpy, pillow, cycler, ...) is staged in the deps
# prefix; the target interpreter already resolves its own site-packages.
"${PY_EXE}" - <<'PYCHECK'
import io, os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ft2font          # freetype-linked extension
import matplotlib._qhull           # qhull-linked extension (triangulation)

fig, ax = plt.subplots(figsize=(2, 2))
ax.plot([0, 1, 2], [0, 1, 0], label="tri")
ax.legend()
buf = io.BytesIO()
fig.savefig(buf, format="png", dpi=50)
assert buf.getbuffer().nbytes > 500, "PNG render produced no payload"

sp = os.path.dirname(os.path.dirname(matplotlib.__file__))
libs = [d for d in os.listdir(sp) if d.endswith(".libs")]
assert not libs, f"vendored {libs} present — the prefix libs were not used"
print("matplotlib", matplotlib.__version__,
      "| ft2font freetype", matplotlib.ft2font.__freetype_version__,
      "| render OK")
PYCHECK
echo "matplotlib-cp311 build + verification complete"
