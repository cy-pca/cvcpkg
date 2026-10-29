# recipes/imath/build.ps1 — build Imath on Windows via CMake + MSVC.
# A shared build's ImathTargets.cmake carries IMATH_DLL to consumers.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DBUILD_TESTING=OFF',
    '-DPYTHON=OFF',
    '-DIMATH_INSTALL_PKG_CONFIG=ON'
)
