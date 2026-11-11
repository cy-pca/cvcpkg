# recipes/nanoflann/build.ps1 — nanoflann's own CMake install (header-only), as build.sh does.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DNANOFLANN_BUILD_EXAMPLES=OFF',
    '-DNANOFLANN_BUILD_TESTS=OFF'
)

$lic = Join-Path $env:CVC_INSTALL_DIR 'share\licenses\nanoflann'
New-Item -ItemType Directory -Force -Path $lic | Out-Null
Copy-Item -Force (Join-Path $env:CVC_SOURCE_DIR 'COPYING') (Join-Path $lic 'COPYING')

foreach ($f in @('include\nanoflann.hpp', 'share\cmake\nanoflann\nanoflannConfig.cmake')) {
    $p = Join-Path $env:CVC_INSTALL_DIR $f
    if (-not (Test-Path -LiteralPath $p)) { throw "nanoflann: $f not installed" }
}
Write-Host 'nanoflann: installed nanoflann.hpp and its CMake package'
