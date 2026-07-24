#!/usr/bin/env bash
# recipes/google-api-core/build.sh — install the pinned pure-Python wheel (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import google_api_core"
