#!/usr/bin/env bash
# recipes/python313t/build.sh — build CPython 3.13.3 (free-threaded, no-GIL).
#
# Identical to python313/build.sh except PYTHON_LDVERSION is set to "3.13t"
# and PYTHON_DISABLE_GIL=1 is passed so build-python.sh appends --disable-gil.
# The resulting interpreter is installed as python3.13t with libpython3.13t.so.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PYTHON_VERSION="3.13.3"
export PYTHON_MINOR="3.13"
export PYTHON_LDVERSION="3.13t"
export PYTHON_DISABLE_GIL=1
source "${SCRIPT_DIR}/../_common/build-python.sh"
