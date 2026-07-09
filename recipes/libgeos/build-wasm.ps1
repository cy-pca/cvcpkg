# recipes/libgeos/build-wasm.ps1 — cross-compile GEOS to wasm via Emscripten.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

Invoke-CvcWasmCMakeBuild @('-DBUILD_TESTING=OFF', '-DBUILD_BENCHMARKS=OFF')
