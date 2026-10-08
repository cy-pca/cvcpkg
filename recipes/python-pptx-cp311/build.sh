#!/usr/bin/env bash
# recipes/python-pptx-cp311/build.sh — build python-pptx 1.0.2 FROM SOURCE
# (import name `pptx`).
#
# WHY FROM SOURCE: a PyPI wheel is somebody else's compiled artifact. cvcpkg
# fetches and sha256-verifies the SDIST (source.type: tarball) instead, and this
# script compiles the wheel with the prefix's own interpreter, then installs it.
# python-pptx is pure Python; its runtime weight is its DEPENDENCIES — lxml
# (native, XML plumbing), Pillow (images), XlsxWriter (embedded chart data) and
# typing-extensions — which are cvcpkg recipes resolved by the depends graph and
# staged into the prefix, so the verify below can `import pptx` and save a deck.
#
# BUILD BACKEND: setuptools (setuptools.build_meta, requires setuptools>=61).
# --no-build-isolation means the backend must ALREADY be importable; it is a
# depends.build edge staged into CVC_BUILD_PREFIX, bridged onto sys.path below.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
_CVC_ENV="${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM:-linux}.sh"
# shellcheck disable=SC1090
[ -f "${_CVC_ENV}" ] && . "${_CVC_ENV}"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/../_common/python-wheel.sh"   # cvc_python_exe, cvc_python_check

# ── 1. Resolve this column's interpreter inside the prefix ──────────────────
: "${CVC_PYTHON_ABI:=cp311}"
: "${CVC_PYTHON_INTERPRETER:=python311}"
PY_EXE="$(cvc_python_exe)"
echo "python-pptx-cp311: building with ${PY_EXE}"

# ── 2. Bridge the build-only backend onto that interpreter's path ───────────
_D="${CVC_PYTHON_ABI#cp}"; _D="${_D%t}"
_PYMM="${_D:0:1}.${_D:1}"                 # cp311 -> 3.12
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
[ -n "${WHEEL}" ] || { echo "python-pptx-cp311: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "python-pptx-cp311: built $(basename "${WHEEL}")"

# ── 4. Install it into this recipe's (empty) staging prefix ─────────────────
"${PY_EXE}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

# ── 5. Verify: build a real .pptx (import pulls in lxml; Presentation().save()
# is exactly the off-catalog pip path this recipe replaces) ─────────────────
# cvc_python_check embeds the snippet in a double-quoted `python -c "..."`, so
# the snippet uses ONLY single quotes (no ", no $, no backticks).  `import pptx`
# resolves lxml/typing-extensions out of the interpreter's own site-packages
# (the runtime deps the depends graph staged into the prefix).
unset PYTHONPATH
cvc_python_check "
import io, pptx
p = pptx.Presentation()
p.slides.add_slide(p.slide_layouts[6])
buf = io.BytesIO()
p.save(buf)
data = buf.getvalue()
assert data[:2] == b'PK', 'pptx (zip) magic missing'
print('python-pptx', pptx.__version__, 'wrote a', len(data), 'byte deck')
"
