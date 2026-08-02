#!/usr/bin/env bash
# recipes/google-crc32c-cp311/build.sh — install the pinned wheel (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import google_crc32c"
