# recipes/log4cplus/build-wasm.ps1 — cross-compile log4cplus to wasm.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"


Invoke-CvcWasmCMakeBuild @(
    '-DLOG4CPLUS_BUILD_TESTING=OFF',
    '-DLOG4CPLUS_BUILD_LOGGINGSERVER=OFF',
    '-DWITH_UNIT_TESTS=OFF'
)
