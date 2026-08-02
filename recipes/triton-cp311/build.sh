#!/usr/bin/env bash
# recipes/triton/build.sh — install the pinned Triton wheel into python311
# site-packages. The GPU kernel compiler for torch.compile on CUDA.
set -euo pipefail
. "$(dirname "$0")/../_common/python-wheel.sh"
cvc_pip_install_wheel
cvc_python_check "import triton; print('triton', triton.__version__)"
