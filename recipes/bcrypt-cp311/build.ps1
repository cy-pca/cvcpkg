# recipes/bcrypt-cp311/build.ps1 — install the pinned cpNN wheel (generated).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"
Invoke-CvcPipInstallWheel
Invoke-CvcPythonCheck 'import bcrypt'
