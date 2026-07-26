#!/usr/bin/env bash
# recipes/nvidia-cuda-runtime-cu12/build.sh — install the pinned NVIDIA CUDA runtime wheel into
# the python311 site-packages (nvidia/ namespace). Lib-only redistributable
# (no importable module), so verify the bundled .so libraries staged.
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
find "${CVC_INSTALL_DIR}" -name '*.so*' -print -quit | grep -q . \
  || { echo "nvidia-cuda-runtime-cu12: no CUDA .so staged" >&2; exit 1; }
echo "nvidia-cuda-runtime-cu12: CUDA runtime libs staged OK"
