#!/usr/bin/env bash
# recipes/pytokens-cp313t/build.sh — install the pinned wheel (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import pytokens"
