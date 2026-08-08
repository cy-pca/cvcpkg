# recipes/log4cplus/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DLOG4CPLUS_BUILD_TESTING=OFF',
    '-DLOG4CPLUS_BUILD_LOGGINGSERVER=OFF',
    '-DWITH_UNIT_TESTS=OFF'
)
