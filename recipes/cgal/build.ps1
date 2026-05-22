# recipes/cgal/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DCGAL_HEADER_ONLY=OFF',
    '-DWITH_CGAL_Core=ON',
    '-DWITH_CGAL_ImageIO=OFF',
    '-DWITH_CGAL_Qt6=OFF'
)
