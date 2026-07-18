# recipes/numpy-cp313t/build.ps1 — install the pinned cp313t NumPy wheel (Windows).
#
# See build.sh; the wheel is already fetched and sha256-verified by cvcpkg.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

. "$PSScriptRoot\..\_common\python-wheel.ps1"

Invoke-CvcPipInstallWheel

Invoke-CvcPythonCheck @'
import numpy as np
a = np.arange(12, dtype=np.float64).reshape(3, 4)
assert a.sum() == 66.0, a.sum()
assert (a @ a.T).shape == (3, 3)
print('numpy', np.__version__, 'from', np.__file__)
'@
