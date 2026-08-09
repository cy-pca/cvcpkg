# recipes/libjpeg-turbo/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

$enableShared = $buildSharedLibs
$enableStatic = if ($buildSharedLibs -eq 'ON') { 'OFF' } else { 'ON' }

Invoke-CvcCMakeBuild @(
    "-DENABLE_SHARED=$enableShared",
    "-DENABLE_STATIC=$enableStatic",
    '-DWITH_TURBOJPEG=ON',
    '-DWITH_JAVA=OFF'
)
