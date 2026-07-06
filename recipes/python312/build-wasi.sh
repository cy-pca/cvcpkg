#!/usr/bin/env bash
# recipes/python312/build-wasi.sh — cross-compile CPython 3.12 for WASI.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-wasi.sh"

export PYTHON_VERSION="3.12.10"
export PYTHON_MINOR="3.12"
source "${SCRIPT_DIR}/../_common/build-python.sh"
