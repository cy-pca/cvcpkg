#!/usr/bin/env bash
# recipes/google-crc32c/build.sh — install one pinned wheel per interpreter (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheels_fanout
cvc_python_check_each "import google_crc32c"
