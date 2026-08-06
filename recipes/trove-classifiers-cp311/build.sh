#!/usr/bin/env bash
# recipes/trove-classifiers-cp311/build.sh — install the pinned wheel (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import trove_classifiers"
