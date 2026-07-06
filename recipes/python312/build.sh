#!/usr/bin/env bash
# recipes/python312/build.sh — build CPython 3.12.10.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PYTHON_VERSION="3.12.10"
export PYTHON_MINOR="3.12"
source "${SCRIPT_DIR}/../_common/build-python.sh"
