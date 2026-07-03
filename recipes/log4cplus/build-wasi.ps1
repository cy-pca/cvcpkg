# recipes/log4cplus/build-wasi.ps1 — cross-compile log4cplus to wasm32-wasi via wasi-sdk.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"


Invoke-CvcWasiCMakeBuild @(
    '-DLOG4CPLUS_BUILD_TESTING=OFF',
    '-DLOG4CPLUS_BUILD_LOGGINGSERVER=OFF',
    '-DWITH_UNIT_TESTS=OFF'
)

# Ensure installed .pc/.cmake files are relocatable.
Invoke-CvcRewriteInstallPaths
