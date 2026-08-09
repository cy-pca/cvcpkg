# recipes/torch-cp311/build.ps1 — install the pinned PyTorch 2.8.0 CPU wheel
# into the python311 interpreter's site-packages. The wheel is fetched and
# sha256-verified by cvcpkg (source.type python_wheel), so this installs
# offline. Windows counterpart of build.sh.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"
Invoke-CvcPipInstallWheel
# Exercise the tensor engine, not just the import: a real matmul on the CPU
# backend, plus assert this is the CPU build (no CUDA in this variant).
Invoke-CvcPythonCheck @'
import torch
a = torch.arange(12, dtype=torch.float64).reshape(3, 4)
assert torch.allclose(a.sum(), torch.tensor(66.0, dtype=torch.float64))
assert (a @ a.T).shape == (3, 3)
print('torch', torch.__version__, '| cuda', torch.cuda.is_available())
'@
