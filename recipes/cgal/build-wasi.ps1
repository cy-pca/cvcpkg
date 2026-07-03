# recipes/cgal/build-wasi.ps1 — cross-compile CGAL to wasm32-wasi via wasi-sdk on Windows.
# Header-only mode; ImageIO and Qt6 disabled.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

Invoke-CvcWasiCMakeBuild -ExtraArgs @(
    '-DCGAL_HEADER_ONLY=ON',
    '-DWITH_CGAL_Core=OFF',
    '-DWITH_CGAL_ImageIO=OFF',
    '-DWITH_CGAL_Qt6=OFF',
    '-DWITH_examples=OFF',
    '-DWITH_demos=OFF',
    '-DWITH_tests=OFF'
)
