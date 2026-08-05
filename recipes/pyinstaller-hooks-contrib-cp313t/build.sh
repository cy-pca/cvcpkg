#!/usr/bin/env bash
# recipes/pyinstaller-hooks-contrib-cp313t/build.sh — install the pinned wheel (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import _pyinstaller_hooks_contrib"
