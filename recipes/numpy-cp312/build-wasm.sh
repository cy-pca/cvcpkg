#!/usr/bin/env bash
# recipes/numpy-cp312/build-wasm.sh — cross-build NumPy 2.4.6 for wasm (Emscripten)
# via meson-python. Proven viable by a local spike (meson cross-configure + emcc
# compile). Key differences from the native build.sh:
#   - NO BLAS (-Dallow-noblas=true): openblas is not built for wasm; numpy uses
#     its internal fallback. matmul/linalg still work, just unaccelerated.
#   - crossenv: a NATIVE python3.12 drives the build, but _PYTHON_SYSCONFIGDATA_NAME
#     points it at the wasm CPython's sysconfigdata so meson-python emits a wasm
#     extension (POSIX-only mechanism — hence wasm builds on the linux fleet, not
#     a Windows host). The wasm sysconfigdata's INCLUDEPY/prefix are stale build
#     paths; rewrite them to CVC_DEPS_PREFIX so meson finds the wasm Python.h/lib.
#   - a meson cross-file (emcc/em++/emar + node exe_wrapper for run-checks).
#   - the wheel carries a wasm platform tag, so native pip cannot install it;
#     extract it into the staging site-packages instead.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/env-wasm.sh"   # emsdk PATH (emcc/em++/emar/node), EMSDK, CVC_JOBS
: "${CVC_JOBS:=$(nproc 2>/dev/null || echo 4)}"

DEPS="${CVC_DEPS_PREFIX:?}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"

# ── (1) NATIVE build toolchain (the wasm libpython can't run meson/Cython) ──
# Provision a COMPLETE native env — python3.12 + Cython + meson-python + meson +
# ninja + pkg-config — into a private host prefix via cvcpkg (all published
# native recipes). This is robust regardless of whether the node's cvcpkg has
# the _collect_host_tools host-tool fix (#60) or bridges the build prefix: the
# fresh host python must have Cython/meson-python importable AND `cython` on PATH
# (meson invokes it as a program), which only co-installing them guarantees.
HOSTENV="${CVC_BUILD_DIR}/hostenv"
_hp="$(uname -s 2>/dev/null || echo Linux)"; case "${_hp}" in Linux) _hp=linux;; Darwin) _hp=macos;; *) _hp=linux;; esac
_ha="$(uname -m 2>/dev/null || echo x86_64)"; case "${_ha}" in x86_64|amd64) _ha=x86_64;; arm64|aarch64) _ha=arm64;; esac
_cvc="cvcpkg"; command -v cvcpkg >/dev/null 2>&1 || _cvc="python3 -m cvcpkg"
echo "numpy(wasm): provisioning native build toolchain (${_hp}/${_ha}) -> ${HOSTENV}"
# NB: `cvcpkg install meson-python-cp312` does NOT pull mesonpy's PyPI runtime
# deps, so list them explicitly — without packaging + pyproject-metadata,
# `import mesonpy` dies with ModuleNotFoundError and pip reports the misleading
# "BackendUnavailable: Cannot import 'mesonpy'". setuptools/wheel back pip wheel.
${_cvc} install python312 cython-cp312 \
    meson-python-cp312 packaging-cp312 pyproject-metadata-cp312 setuptools-cp312 wheel-cp312 \
    meson ninja pkg-config \
    --platform "${_hp}" --arch "${_ha}" --config release --link shared \
    --prefix "${HOSTENV}" --no-fallback-to-source >&2
PY_NATIVE="${HOSTENV}/bin/python3.12"
[ -x "${PY_NATIVE}" ] || { echo "numpy(wasm): FATAL — native python3.12 not provisioned in ${HOSTENV}" >&2; ls -la "${HOSTENV}/bin" >&2 2>/dev/null; exit 1; }
export PATH="${HOSTENV}/bin:${PATH}"
# meson looks for `cython`/`cython3` as programs; ensure both names resolve.
if [ ! -x "${HOSTENV}/bin/cython" ]; then
    for _cy in "${HOSTENV}"/bin/cython3 "${HOSTENV}"/bin/cython3.*; do
        [ -x "${_cy}" ] && ln -sf "$(basename "${_cy}")" "${HOSTENV}/bin/cython" && break
    done
fi
_BRIDGE="${HOSTENV}/lib/python3.12/site-packages"
echo "numpy(wasm): host interpreter ${PY_NATIVE}; cython=$(command -v cython 2>/dev/null || echo MISSING); meson=$(command -v meson 2>/dev/null || echo MISSING)"

# ── (2) wasm CPython target: un-stale its cross sysconfigdata ───────────────
_SYS="_sysconfigdata__emscripten_wasm32-emscripten"
_SYS_SRC="${DEPS}/lib/python3.12/${_SYS}.py"
[ -f "${_SYS_SRC}" ] || { echo "numpy(wasm): FATAL — wasm sysconfigdata missing at ${_SYS_SRC} (install python312 wasm first)" >&2; exit 1; }
_CROSSSYS="${CVC_BUILD_DIR}/crosssys"; mkdir -p "${_CROSSSYS}"
cp "${_SYS_SRC}" "${_CROSSSYS}/"
# The build-time absolute prefix (/tmp/cvcpkg-builder/.../install) is baked into
# INCLUDEPY/prefix/exec_prefix/LIBDIR/LIBPL; point them at the real deps prefix.
sed -i -E "s#/tmp/cvcpkg-builder/[^'\"]*/install#${DEPS}#g" "${_CROSSSYS}/${_SYS}.py"
# Do NOT set _PYTHON_SYSCONFIGDATA_NAME globally: it makes the HOST python's
# site/sysconfig resolve to the wasm purelib and breaks meson-python's own import
# ("Cannot import mesonpy"). Expose the crossenv ONLY to meson, via a wrapper
# 'cross-python' named in the cross-file [binaries] python: meson runs it to
# introspect the TARGET interpreter (gets the wasm sysconfig), while pip / mesonpy
# / Cython run the plain host python.
_XPY="${CVC_BUILD_DIR}/cross-python"
cat > "${_XPY}" <<EOF
#!/bin/sh
export _PYTHON_SYSCONFIGDATA_NAME="${_SYS}"
export PYTHONPATH="${_CROSSSYS}\${PYTHONPATH:+:\$PYTHONPATH}"
exec "${PY_NATIVE}" "\$@"
EOF
chmod +x "${_XPY}"
: "${_BRIDGE:=}"   # (host mesonpy/Cython live in PY_NATIVE's own site-packages)
# Fallback include path for the wasm Python.h in case a probe reads a stale -I.
export CPATH="${DEPS}/include/python3.12${CPATH:+:${CPATH}}"

# ── (3) meson emscripten cross-file ─────────────────────────────────────────
_NODE="$(command -v node 2>/dev/null || ls "${EMSDK}"/node/*/bin/node 2>/dev/null | head -1)"
_CROSS="${CVC_BUILD_DIR}/emscripten-cross.txt"
cat > "${_CROSS}" <<EOF
[binaries]
c = 'emcc'
cpp = 'em++'
ar = 'emar'
ranlib = 'emranlib'
exe_wrapper = '${_NODE}'
python = '${_XPY}'
python3 = '${_XPY}'

[built-in options]
c_args = ['-fPIC']
cpp_args = ['-fPIC']

[host_machine]
system = 'emscripten'
cpu_family = 'wasm32'
cpu = 'wasm32'
endian = 'little'
EOF

# ── (4) build the wheel (from source, offline, no BLAS) ─────────────────────
WHEELOUT="${CVC_BUILD_DIR}/wheelhouse"; mkdir -p "${WHEELOUT}"
_dump_meson_log() {
    local _log="${CVC_BUILD_DIR}/meson/meson-logs/meson-log.txt"
    echo "----- meson-log.txt tail -----" >&2
    [ -f "${_log}" ] && tail -n 120 "${_log}" >&2 || echo "(no meson log)" >&2
}
if ! "${PY_NATIVE}" -m pip wheel \
    --no-build-isolation --no-deps --no-index --no-cache-dir \
    --wheel-dir "${WHEELOUT}" \
    -C setup-args=--cross-file="${_CROSS}" \
    -C setup-args=-Dallow-noblas=true \
    -C builddir="${CVC_BUILD_DIR}/meson" \
    -C compile-args=-j"${CVC_JOBS}" \
    "${CVC_SOURCE_DIR}"; then
    _dump_meson_log
    exit 1
fi

WHEEL="$(find "${WHEELOUT}" -maxdepth 1 -name 'numpy-*.whl' | head -1)"
[ -n "${WHEEL}" ] || { echo "numpy(wasm): no wheel produced" >&2; exit 1; }
echo "numpy(wasm): built $(basename "${WHEEL}")"

# ── (5) install by EXTRACTION (native pip rejects the wasm platform tag) ────
# stage_bundle ships the whole CVC_INSTALL_DIR, so extract only into
# site-packages to keep the staged tree pure.
DEST="${CVC_INSTALL_DIR}/lib/python3.12/site-packages"; mkdir -p "${DEST}"
"${PY_NATIVE}" -m zipfile -e "${WHEEL}" "${DEST}"
echo "numpy(wasm): extracted into ${DEST}"
find "${CVC_INSTALL_DIR}" -maxdepth 5 \( -name '_multiarray_umath*.so' -o -name 'version.py' -path '*numpy*' \) -print | head
[ -d "${DEST}/numpy" ] || { echo "numpy(wasm): FATAL — numpy/ not staged" >&2; exit 1; }
echo "numpy(wasm) build complete (no-BLAS static wasm)"
