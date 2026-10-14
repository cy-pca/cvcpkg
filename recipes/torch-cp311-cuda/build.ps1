# recipes/torch-cp311-cuda/build.ps1 — install the pinned PyTorch 2.8.0 CUDA 12.8
# wheel into the python311 interpreter's site-packages. The wheel is fetched and
# sha256-verified by cvcpkg (source.type python_wheel), so this installs
# offline. Windows counterpart of build.sh: the Windows wheel bundles the CUDA
# runtime DLLs under torch\lib, so there are no nvidia-* wheels to put on a path.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"
Invoke-CvcPipInstallWheel
# Exercise the tensor engine on the CPU backend and assert this is the CUDA
# build. torch imports without a GPU or driver, so this runs on any builder;
# torch.cuda.is_available() is printed, not asserted.
Invoke-CvcPythonCheck @'
import torch
a = torch.arange(12, dtype=torch.float64).reshape(3, 4)
assert torch.allclose(a.sum(), torch.tensor(66.0, dtype=torch.float64))
assert (a @ a.T).shape == (3, 3)
assert torch.version.cuda is not None, 'expected a CUDA build (torch.version.cuda is None)'
print('torch', torch.__version__, '| cuda', torch.version.cuda, '| avail', torch.cuda.is_available())
'@
