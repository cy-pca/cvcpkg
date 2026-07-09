# recipes/libspatialindex/build-wasi.ps1 — cross-compile libspatialindex to wasm32-wasi via wasi-sdk.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

Invoke-CvcWasiCMakeBuild @('-DBUILD_TESTING=OFF')
