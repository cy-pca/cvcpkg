# recipes/cgal/build-wasm.ps1 — cross-compile CGAL to wasm.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"


Invoke-CvcWasmCMakeBuild @(
    '-DCGAL_HEADER_ONLY=OFF',
    '-DWITH_CGAL_Core=ON',
    '-DWITH_CGAL_ImageIO=OFF',
    '-DWITH_CGAL_Qt6=OFF'
)
