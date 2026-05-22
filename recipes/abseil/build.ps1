# recipes/abseil/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DABSL_BUILD_TESTING=OFF',
    '-DABSL_USE_GOOGLETEST_HEAD=OFF',
    '-DCMAKE_CXX_STANDARD=17'
)
