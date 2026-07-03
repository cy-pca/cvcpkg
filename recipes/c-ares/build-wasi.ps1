# recipes/c-ares/build-wasi.ps1 — cross-compile c-ares to wasm32-wasi via wasi-sdk.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"


Invoke-CvcWasiCMakeBuild @(
    '-DCARES_BUILD_TESTS=OFF',
    '-DCARES_BUILD_TOOLS=OFF'
)
