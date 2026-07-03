# recipes/re2/build-wasi.ps1 — cross-compile RE2 to wasm32-wasi via wasi-sdk.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"


Invoke-CvcWasiCMakeBuild @(
    '-DRE2_BUILD_TESTING=OFF',
    '-DCMAKE_CXX_STANDARD=17'
)
