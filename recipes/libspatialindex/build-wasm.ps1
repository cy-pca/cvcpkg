# recipes/libspatialindex/build-wasm.ps1 — cross-compile libspatialindex to wasm via Emscripten.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"

Invoke-CvcWasmCMakeBuild @('-DBUILD_TESTING=OFF')
