#!/usr/bin/env bash
# recipes/msal-extensions-cp312/build.sh — install the pinned wheel (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import msal_extensions"
