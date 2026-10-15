# recipes/ruff/build.ps1 — Windows counterpart of build.sh: install the pinned
# ruff binary wheel (sha256-verified by cvcpkg, source.type python_wheel) into
# the python311 prefix. pip puts the `ruff` proxy module in Lib\site-packages and
# the Rust binary in Scripts\ruff.exe.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"
Invoke-CvcPipInstallWheel
# Prove the binary runs, not just that the proxy module imports.
$ruff = Join-Path $env:CVC_INSTALL_DIR 'Scripts\ruff.exe'
if (-not (Test-Path $ruff)) { throw "ruff.exe not found at $ruff" }
& $ruff --version
if ($LASTEXITCODE -ne 0) { throw "ruff --version failed ($LASTEXITCODE)" }
Invoke-CvcPythonCheck 'import ruff'
