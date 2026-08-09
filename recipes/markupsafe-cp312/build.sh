#!/usr/bin/env bash
# recipes/markupsafe-cp312/build.sh — build MarkupSafe 3.0.3 FROM SOURCE for the cp312
# interpreter column of the per-interpreter wheel matrix.
#
# WHY FROM SOURCE (not the PyPI wheel): a published wheel is a binary somebody
# else compiled, and PyPI has no FreeBSD/OpenBSD/NetBSD wheel for markupsafe at
# all — that absence is the only reason this package was missing on the BSDs.
# Compiling the sdist makes every platform column come out of one toolchain.
#
# MECHANIC: `pip wheel --no-build-isolation --no-deps --no-index <sdist>` with the
# PEP-517 backend already provisioned into the prefix by the depends.build edges,
# then pip-install the resulting wheel into this recipe's staging prefix so the
# dist-info/RECORD/METADATA are real and pip's resolver later sees MarkupSafe as
# satisfied. This script does NOT re-solve the build closure — it assumes the
# declared deps are present and fails loudly when an import or a probe is missing.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # toolchain, CVC_JOBS
# shellcheck disable=SC1091
source "${SCRIPT_DIR}/../_common/python-wheel.sh"           # cvc_python_exe, ...

# ── 1. Resolve the prefix interpreter for this column ───────────────────────
# python.interpreter/python.abi are exported by the builder; keep defaults so the
# script stays runnable standalone.
: "${CVC_PYTHON_ABI:=cp312}"
: "${CVC_PYTHON_INTERPRETER:=python312}"
PY="$(cvc_python_exe)"
DEPS="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
BLD="${CVC_BUILD_PREFIX:-${DEPS}}"
# cvc_interp_version keeps the free-threaded suffix (python313t -> 3.13t), which
# is also the site-packages directory name — do not re-derive it from the ABI tag.
PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
echo "markupsafe-cp312: building with ${PY} (python${PYMM})"

# ── 2. Bridge BUILD-only python packages into the DEPS-prefix interpreter ────
# cvc_python_exe runs the interpreter from CVC_DEPS_PREFIX, which imports only its
# OWN site-packages. depends.build python columns land in CVC_BUILD_PREFIX, so
# --no-build-isolation cannot import them without this PYTHONPATH bridge — and the
# bridge is also what lets setuptools >= 77 win over the 65.5 the base
# interpreter bundles, because PYTHONPATH precedes site-packages in sys.path.
export PATH="${BLD}/bin:${DEPS}/bin:${PATH}"
export PYTHONPATH="${BLD}/lib/python${PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
"${PY}" -c 'import setuptools; print("markupsafe-cp312: setuptools", setuptools.__version__, setuptools.__file__)'

# ── 3. Refuse the silent pure-Python fallback ────────────────────────────
# markupsafe's setup.py catches a failed C compile and RE-RUNS setup() with
# ext_modules=[], producing a wheel that installs cleanly and is quietly much
# slower — the worst possible outcome on a build farm, because nothing fails and
# nothing is logged as an error. CIBUILDWHEEL=1 is upstream's own switch for
# "this is a binary wheel build, do not fall back"; with it set the compile error
# propagates. The check at the end re-asserts the extension really is there, so a
# future setup.py that stops honouring the variable cannot slip past either.
export CIBUILDWHEEL=1

# ── 4. Build the wheel from the sdist (offline, no build isolation) ────────
# --no-deps so pip never reaches past cvcpkg's graph; --no-index so it cannot
# resolve anything over the network (this is what makes air-gapped builds work);
# --no-build-isolation so the backend uses the prefix's setuptools rather than
# downloading its own.
WHEELHOUSE="${CVC_BUILD_DIR}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY}" -m pip wheel \
    --no-deps \
    --no-build-isolation \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

# Plain glob rather than `find -print -quit`: OpenBSD's find has no -quit, and a
# `find | head` pipeline trips `set -o pipefail` when head closes the pipe early.
shopt -s nullglob
_wheels=( "${WHEELHOUSE}"/*.whl )
shopt -u nullglob
WHEEL="${_wheels[0]:-}"
[ -n "${WHEEL}" ] || { echo "markupsafe-cp312: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "markupsafe-cp312: built $(basename "${WHEEL}")"

# ── 5. Install into THIS recipe's staging prefix ──────────────────────────
# stage_bundle ships the ENTIRE CVC_INSTALL_DIR tree (package.files is not a
# filter), so installing --prefix into the initially-empty per-recipe dir IS the
# packaging contract: nothing but this package can land there.
"${PY}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

# Locate the staged site-packages by globbing the known install schemes (same
# reason as above: no `find -quit`). An unmatched glob stays literal, so the
# `-d` test simply fails for it.
SITE_PACKAGES=""
for _cand in "${CVC_INSTALL_DIR}"/lib/python*/site-packages \
             "${CVC_INSTALL_DIR}"/lib64/python*/site-packages \
             "${CVC_INSTALL_DIR}"/Lib/site-packages; do
    if [ -d "${_cand}" ]; then SITE_PACKAGES="${_cand}"; break; fi
done
[ -n "${SITE_PACKAGES}" ] || {
    echo "markupsafe-cp312: no site-packages under ${CVC_INSTALL_DIR} after pip install" >&2
    ls -la "${CVC_INSTALL_DIR}" >&2 || true
    exit 1
}
echo "markupsafe-cp312: staged into ${SITE_PACKAGES}"

# ── 6. No RPATH pass, on purpose ────────────────────────────────────────
# markupsafe._speedups links nothing but libc — there is no cvcpkg shared
# library for it to find at import — so unlike cffi/pyyaml/h5py there is nothing
# here to stamp. cvcpkg's own post-build pass still prepends a bare $ORIGIN on the
# ELF platforms that expand it; that is enough.
command -v cvc_rewrite_install_paths >/dev/null 2>&1 && cvc_rewrite_install_paths || true

# ── 7. Verification ────────────────────────────────────────────────────────
# Prove the compiled extension is present and actually does the work.
export PYTHONPATH="${SITE_PACKAGES}${PYTHONPATH:+:${PYTHONPATH}}"
# Nothing external to load: no LD_LIBRARY_PATH widening needed.
# A free-threaded column must be proven WITHOUT the GIL: an extension that only
# imports under a re-enabled GIL has not been shown to work without one, and
# cvcpkg would be publishing an unproven guarantee (see _common/python-wheel.sh).
PYARGS=()
if cvc_python_is_free_threaded; then
    export PYTHON_GIL=0
    PYARGS+=(-X gil=0)
fi
"${PY}" ${PYARGS[@]+"${PYARGS[@]}"} - <<'PYCHECK'
import sys, sysconfig
if sysconfig.get_config_var("Py_GIL_DISABLED"):
    assert not sys._is_gil_enabled(), "GIL re-enabled at runtime; no-GIL support unproven"
    print("GIL disabled:", not sys._is_gil_enabled())

import markupsafe
# ImportError here means setup.py took its silent pure-Python path and we were
# about to ship a markupsafe with no C accelerator in it.
from markupsafe import _speedups

assert _speedups.__file__.endswith((".so", ".pyd", ".dylib")), _speedups.__file__
# Not just "the module exists" — the escape path actually bound to the C one.
assert markupsafe._escape_inner is _speedups._escape_inner, "escape path is not the C one"

assert markupsafe.escape("<a href='x'>&") == "&lt;a href=&#39;x&#39;&gt;&amp;"
assert markupsafe.Markup("<b>{}</b>").format("<i>") == "<b>&lt;i&gt;</b>"
assert markupsafe.escape(markupsafe.Markup("<b>ok</b>")) == "<b>ok</b>"

print("markupsafe   :", markupsafe.__file__)
print("_speedups    :", _speedups.__file__)
PYCHECK

echo "markupsafe-cp312: build + verification complete"
