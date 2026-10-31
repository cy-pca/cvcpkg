#!/usr/bin/env bash
# recipes/pythreejs-cp311/build.sh — install the pinned wheel (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import pythreejs; pythreejs.BoxGeometry(width=1, height=1, depth=1)"
