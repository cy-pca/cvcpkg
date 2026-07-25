# recipes/pydantic-core/build.ps1 — one wheel per interpreter (generated).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"
Invoke-CvcPipInstallWheelsFanout
Invoke-CvcPythonCheckEach 'import pydantic_core'
