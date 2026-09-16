# recipes/contourpy-cp311/build.ps1 — Windows counterpart of build.sh: install
# the pinned prebuilt contourpy win_amd64 wheel (source.type python_wheel).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"
Invoke-CvcPipInstallWheel
Invoke-CvcPythonCheck @'
import contourpy
print('contourpy', contourpy.__version__)
'@
