#!/usr/bin/env bash
# recipes/python313/build-wasi.sh — cross-compile CPython 3.13 for WASI.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

export PYTHON_VERSION="3.13.3"
export PYTHON_MINOR="3.13"
source "${SCRIPT_DIR}/../_common/build-python.sh"
