#!/usr/bin/env bash
# recipes/mpmath-cp311/build.sh — install the pinned noarch mpmath wheel into python311's
# site-packages (a runtime dep of torch-cp311). Generated pattern.
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import mpmath"
