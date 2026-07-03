# recipes/fftw3/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DBUILD_TESTS=OFF',
    '-DENABLE_THREADS=ON',
    '-DWITH_COMBINED_THREADS=ON'
)
