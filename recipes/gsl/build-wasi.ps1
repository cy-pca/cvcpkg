# recipes/gsl/build-wasi.ps1 — cross-compile GSL to wasm32-wasi via wasi-sdk.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

$env:CC_FOR_BUILD = 'gcc'

Invoke-CvcWasiAutotoolsBuild

# Ensure installed .pc/.cmake files are relocatable.
Invoke-CvcRewriteInstallPaths
