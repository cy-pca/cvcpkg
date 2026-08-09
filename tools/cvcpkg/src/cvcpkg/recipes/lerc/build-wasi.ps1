# recipes/lerc/build-wasi.ps1 — cross-compile LERC to wasm32-wasi via wasi-sdk.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

Invoke-CvcWasiCMakeBuild
