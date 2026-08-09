# recipes/re2/build-wasm.ps1 — cross-compile RE2 to wasm.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"


Invoke-CvcWasmCMakeBuild @(
    '-DRE2_BUILD_TESTING=OFF',
    '-DCMAKE_CXX_STANDARD=17'
)
