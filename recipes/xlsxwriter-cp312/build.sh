#!/usr/bin/env bash
# recipes/xlsxwriter-cp312/build.sh — build XlsxWriter 3.2.9 FROM SOURCE.
#
# WHY FROM SOURCE: a PyPI wheel is somebody else's compiled artifact. cvcpkg
# fetches and sha256-verifies the SDIST (source.type: tarball) instead, and this
# script compiles the wheel with the prefix's own interpreter, then installs it —
# so the bundle contains only things cvcpkg built. XlsxWriter is pure Python (no
# native deps, stdlib only), so this is a straightforward setuptools sdist build.
#
# BUILD BACKEND: setuptools (setuptools.build_meta). --no-build-isolation means
# pip does NOT download the backend into a throwaway venv (non-hermetic, and
# impossible offline): it must ALREADY be importable. setuptools-cp312 /
# wheel-cp312 are declared as depends.build edges, staged into CVC_BUILD_PREFIX,
# and step 2's PYTHONPATH bridge is what makes them importable.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
_CVC_ENV="${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM:-linux}.sh"
# shellcheck disable=SC1090
[ -f "${_CVC_ENV}" ] && . "${_CVC_ENV}"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/../_common/python-wheel.sh"   # cvc_python_exe, cvc_python_check

# ── 1. Resolve this column's interpreter inside the prefix ──────────────────
: "${CVC_PYTHON_ABI:=cp312}"
: "${CVC_PYTHON_INTERPRETER:=python312}"
PY_EXE="$(cvc_python_exe)"
echo "xlsxwriter-cp312: building with ${PY_EXE}"

# ── 2. Bridge the build-only backend onto that interpreter's path ───────────
_D="${CVC_PYTHON_ABI#cp}"; _D="${_D%t}"
_PYMM="${_D:0:1}.${_D:1}"                 # cp312 -> 3.12
if [ -n "${CVC_BUILD_PREFIX:-}" ]; then
    _BP_SITE="${CVC_BUILD_PREFIX}/lib/python${_PYMM}/site-packages"
    export PYTHONPATH="${_BP_SITE}${PYTHONPATH:+:${PYTHONPATH}}"
fi

# ── 3. Build the wheel from the extracted sdist ─────────────────────────────
WHEELHOUSE="${CVC_BUILD_DIR:-${CVC_SOURCE_DIR}}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY_EXE}" -m pip wheel \
    --no-build-isolation \
    --no-deps \
    --no-index \
    --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" \
    "${CVC_SOURCE_DIR}"

WHEEL=""
for WHEEL in "${WHEELHOUSE}"/*.whl; do [ -e "${WHEEL}" ] && break; WHEEL=""; done
[ -n "${WHEEL}" ] || { echo "xlsxwriter-cp312: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "xlsxwriter-cp312: built $(basename "${WHEEL}")"

# ── 4. Install it into this recipe's (empty) staging prefix ─────────────────
"${PY_EXE}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

# ── 5. Verify the staged package imports and actually writes a workbook ─────
# NOTE: cvc_python_check embeds the snippet in a double-quoted `python -c "..."`,
# so the snippet uses ONLY single quotes (no ", no $, no backticks).
unset PYTHONPATH
cvc_python_check "
import io, xlsxwriter
buf = io.BytesIO()
wb = xlsxwriter.Workbook(buf)
ws = wb.add_worksheet()
ws.write(0, 0, 'cvcpkg')
ws.write_number(0, 1, 42)
wb.close()
data = buf.getvalue()
assert data[:2] == b'PK', 'xlsx (zip) magic missing'
print('xlsxwriter', xlsxwriter.__version__, 'wrote', len(data), 'bytes')
"
