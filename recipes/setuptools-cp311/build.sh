#!/usr/bin/env bash
# recipes/setuptools-cp311/build.sh — install the pinned noarch setuptools wheel into the
# python311 interpreter's site-packages. A build-time backend for cvcpkg's
# from-source Python packages; not shipped to users. (Generated pattern.)
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import setuptools; print('setuptools-cp311', getattr(__import__('setuptools'), '__version__', 'ok'))"
