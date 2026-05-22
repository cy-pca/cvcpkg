# recipes/tiff/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-Dtiff-tests=OFF',
    '-Dtiff-tools=OFF',
    '-Dtiff-contrib=OFF',
    '-Dtiff-docs=OFF'
)
