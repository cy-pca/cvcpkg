#!/usr/bin/env bash
# recipes/python311/build.sh — build CPython 3.11.13.
# Build logic lives in the shared helper; this file only pins the version.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PYTHON_VERSION="3.11.13"
export PYTHON_MINOR="3.11"
source "${SCRIPT_DIR}/../_common/build-python.sh"
