#!/usr/bin/env bash
# recipes/google-cloud-storage/build.sh — install the pinned pure-Python wheel (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import google"
