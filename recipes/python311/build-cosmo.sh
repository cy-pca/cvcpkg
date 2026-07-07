#!/usr/bin/env bash
# recipes/python311/build-cosmo.sh — cross-compile CPython 3.11 for Cosmo.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

export PYTHON_VERSION="3.11.13"
export PYTHON_MINOR="3.11"
source "${SCRIPT_DIR}/../_common/build-python.sh"
