#!/usr/bin/env bash
# recipes/mypy-extensions/build.sh — install the pinned noarch mypy-extensions wheel into the
# reference interpreter's site-packages, then fan it into every interpreter.
# A build-time lint/test tool for cvcpkg recipes; not shipped to users.
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import mypy_extensions; print('mypy-extensions', getattr(__import__('mypy_extensions'), '__version__', 'ok'))"
