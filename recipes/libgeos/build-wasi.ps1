# recipes/libgeos/build-wasi.ps1 — cross-compile GEOS to wasm32-wasi via wasi-sdk.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

Invoke-CvcWasiCMakeBuild @('-DBUILD_TESTING=OFF', '-DBUILD_BENCHMARKS=OFF')
