# recipes/c-ares/build-wasm.ps1 — cross-compile c-ares to wasm.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"


Invoke-CvcWasmCMakeBuild @(
    '-DCARES_BUILD_TESTS=OFF',
    '-DCARES_BUILD_TOOLS=OFF'
)
