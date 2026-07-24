#!/usr/bin/env bash
# recipes/asyncpg-cp311/build.sh — install the pinned cpNN wheel (generated).
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import asyncpg"
