#!/usr/bin/env bash
# recipes/pyasn1-cp313t/build.sh — install the pinned wheel (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import pyasn1"
