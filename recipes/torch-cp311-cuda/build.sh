#!/usr/bin/env bash
# recipes/torch-cp311-cuda/build.sh — install the pinned PyTorch 2.8.0 CUDA 12.8
# wheel into python311's site-packages. The NVIDIA CUDA runtime + triton come
# from the depends graph (staged in the build prefix). Installs offline.
set -euo pipefail

. "$(dirname "$0")/../_common/python-wheel.sh"

cvc_pip_install_wheel

# torch loads the CUDA runtime .so's shipped by the nvidia-*-cu12 packages (in
# the deps prefix, under nvidia/<pkg>/lib). torch's install tree is isolated
# during the build, so its RUNPATH-relative path to those libs doesn't resolve
# and its preloader doesn't cover every lib (e.g. cudnn -> cublas). Put every
# nvidia/<pkg>/lib on the loader path so `import torch` resolves them all.
_nv_libdirs="$(find "${CVC_DEPS_PREFIX}"/lib/python3.11*/site-packages/nvidia \
    -maxdepth 2 -type d -name lib 2>/dev/null | paste -sd:)"
export LD_LIBRARY_PATH="${_nv_libdirs}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

# Import loads the CUDA runtime; assert this is the CUDA build. torch imports
# fine on a GPU-less builder (cuda.is_available() may be False without a driver),
# so gate the check on the build string, not device availability.
cvc_python_check "
import torch
a = torch.arange(12, dtype=torch.float64).reshape(3, 4)
assert torch.allclose(a.sum(), torch.tensor(66.0, dtype=torch.float64))
assert (a @ a.T).shape == (3, 3)
assert torch.version.cuda is not None, 'expected a CUDA build (torch.version.cuda is None)'
print('torch', torch.__version__, '| cuda', torch.version.cuda, '| avail', torch.cuda.is_available())
"
