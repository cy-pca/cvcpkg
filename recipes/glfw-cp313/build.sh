#!/usr/bin/env bash
# recipes/glfw-cp313/build.sh — build pyGLFW 2.11.0 (PyPI `glfw`) FROM SOURCE.
#
# Hand-written, but the same steps as a generated from-source column (see
# recipes/pyopengl-cp313/build.sh for why each one is there): build the wheel
# from the patched sdist with the prefix's own interpreter, install it into the
# empty staging prefix, then check it imports.  The check is what differs: it
# asserts that `import glfw` loaded the prefix's GLFW 3.4 (the glfw recipe),
# not some other copy on the builder.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# Platform toolchain/env.  Sourced conditionally: a noarch (platform: any)
# column can be claimed by a builder whose platform ships no env-*.sh.
_CVC_ENV="${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM:-linux}.sh"
# shellcheck disable=SC1090
[ -f "${_CVC_ENV}" ] && . "${_CVC_ENV}"
# shellcheck disable=SC1091
. "${SCRIPT_DIR}/../_common/python-wheel.sh"   # cvc_python_exe, cvc_python_check

: "${CVC_PYTHON_ABI:=cp313}"
: "${CVC_PYTHON_INTERPRETER:=python313}"
PY_EXE="$(cvc_python_exe)"
echo "glfw-cp313: building with ${PY_EXE}"

# The build-only backend (setuptools, wheel) lives in the build prefix.
_D="${CVC_PYTHON_ABI#cp}"
_PYMM="${_D:0:1}.${_D:1}"                 # cp313 -> 3.13
if [ -n "${CVC_BUILD_PREFIX:-}" ]; then
    export PYTHONPATH="${CVC_BUILD_PREFIX}/lib/python${_PYMM}/site-packages${PYTHONPATH:+:${PYTHONPATH}}"
fi

WHEELHOUSE="${CVC_BUILD_DIR:-${CVC_SOURCE_DIR}}/wheelhouse"
mkdir -p "${WHEELHOUSE}"
"${PY_EXE}" -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir \
    --wheel-dir "${WHEELHOUSE}" "${CVC_SOURCE_DIR}"

WHEEL=""
for WHEEL in "${WHEELHOUSE}"/*.whl; do [ -e "${WHEEL}" ] && break; WHEEL=""; done
[ -n "${WHEEL}" ] || { echo "glfw-cp313: no wheel produced under ${WHEELHOUSE}" >&2; exit 1; }
echo "glfw-cp313: built $(basename "${WHEEL}")"

"${PY_EXE}" -m pip install --no-index --no-deps --no-compile --ignore-installed \
    --prefix "${CVC_INSTALL_DIR}" "${WHEEL}"

# The staged package sits in CVC_INSTALL_DIR, which holds no libglfw, so the
# patched loader's second candidate, sys.base_prefix (the deps prefix holding
# the interpreter and the glfw recipe), is the one that must load.
unset PYTHONPATH
read -r -d '' CHECK <<'PY' || true
import os, sys, glfw
rel = {'win32': ('bin', 'glfw3.dll'), 'darwin': ('lib', 'libglfw.3.dylib')}
want = os.path.join(sys.base_prefix, *rel.get(sys.platform, ('lib', 'libglfw.so.3')))
assert os.path.samefile(glfw._glfw._name, want), (glfw._glfw._name, want)
assert glfw.get_version()[:2] == (3, 4), glfw.get_version()
print('pyGLFW', glfw.__version__, 'loaded', glfw._glfw._name, glfw.get_version_string().decode())
PY
cvc_python_check "${CHECK}"
