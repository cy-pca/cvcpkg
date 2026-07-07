#!/usr/bin/env bash
# recipes/libiimod/build-cosmo.sh — cross-compile libiimod with Cosmopolitan.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-cosmo.sh"

cvc_cmake_build
