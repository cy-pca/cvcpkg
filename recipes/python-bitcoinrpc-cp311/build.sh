#!/usr/bin/env bash
# recipes/python-bitcoinrpc-cp311/build.sh — build python-bitcoinrpc 1.0 FROM SOURCE (hand-written).
#
# HAND-WRITTEN, not generated: PyPI has only the 2016 sdist for 1.0 (no wheel),
# and tools/gen_python_recipes.py derives column viability from published wheels,
# so it treats a wheel-less release as fatal. The build itself is the generator's
# pure-Python shape: setup.py is a plain `distutils.core.setup()`, which pip runs
# through setuptools' legacy PEP-517 backend (setuptools supplies `distutils` on
# 3.12+ via its _distutils_hack shim).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
_CVC_ENV="${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM:-linux}.sh"
# shellcheck disable=SC1090
[ -f "${_CVC_ENV}" ] && . "${_CVC_ENV}"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/../_common/python-wheel.sh"

: "${CVC_PYTHON_ABI:=cp311}"
: "${CVC_PYTHON_INTERPRETER:=python311}"
PY_EXE="$(cvc_python_exe)"
echo "python-bitcoinrpc-cp311: building with ${PY_EXE}"

_PYMM="$(cvc_interp_version "${CVC_PYTHON_INTERPRETER}")"
if [ -n "${CVC_BUILD_PREFIX:-}" ]; then
    export PYTHONPATH="${CVC_BUILD_PREFIX}/lib/python${_PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
fi

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
[ -n "${WHEEL}" ] || { echo "python-bitcoinrpc-cp311: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "python-bitcoinrpc-cp311: built $(basename "${WHEEL}")"
case "$(basename "${WHEEL}")" in
    *-py3-none-any.whl) ;;
    *) echo "python-bitcoinrpc-cp311: expected a py3-none-any wheel" >&2; exit 1 ;;
esac

"${PY_EXE}" -m pip install \
    --no-index \
    --no-deps \
    --no-compile \
    --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" \
    "${WHEEL}"

unset PYTHONPATH
cvc_python_check "$(cat <<'PYCHECK'
from bitcoinrpc.authproxy import AuthServiceProxy, JSONRPCException
proxy = AuthServiceProxy("http://user:pass@127.0.0.1:1", timeout=2)
try:
    proxy.getblockcount()
except (OSError, ConnectionError) as e:
    print("bitcoinrpc: no node on 127.0.0.1:1, as expected:", type(e).__name__)
else:
    raise AssertionError("getblockcount() against a closed port returned?")
assert issubclass(JSONRPCException, Exception)
PYCHECK
)"
