# recipes/abseil/build-wasm.ps1 — cross-compile Abseil to wasm.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"


Invoke-CvcWasmCMakeBuild @(
    '-DABSL_BUILD_TESTING=OFF',
    '-DABSL_USE_GOOGLETEST_HEAD=OFF',
    '-DCMAKE_CXX_STANDARD=17'
)
