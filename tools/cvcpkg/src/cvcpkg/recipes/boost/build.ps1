# recipes/boost/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DBOOST_ENABLE_CMAKE=ON',
    '-DBUILD_TESTING=OFF',
    '-DBOOST_INSTALL_LAYOUT=system'
)
