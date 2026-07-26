#!/usr/bin/env bash
# recipes/pytest/build.sh — install the pinned noarch pytest wheel into the
# reference interpreter's site-packages, then fan it into every interpreter.
# A build-time lint/test tool for cvcpkg recipes; not shipped to users.
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import pytest; print('pytest', getattr(__import__('pytest'), '__version__', 'ok'))"
