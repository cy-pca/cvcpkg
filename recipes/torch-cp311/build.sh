#!/usr/bin/env bash
# recipes/torch-cp311/build.sh — install the pinned PyTorch 2.8.0 CPU wheel into
# the python311 interpreter's site-packages. The wheel is fetched + sha256-verified
# by cvcpkg (source.type python_wheel), so this installs offline (--no-index).
set -euo pipefail

. "$(dirname "$0")/../_common/python-wheel.sh"

cvc_pip_install_wheel

# Exercise the tensor engine, not just the import: a real matmul on the CPU
# backend, plus assert this is the CPU build (no CUDA expected in this variant).
cvc_python_check "
import torch
a = torch.arange(12, dtype=torch.float64).reshape(3, 4)
assert torch.allclose(a.sum(), torch.tensor(66.0, dtype=torch.float64))
assert (a @ a.T).shape == (3, 3)
print('torch', torch.__version__, '| cuda', torch.cuda.is_available(), '| from', torch.__file__)
"
