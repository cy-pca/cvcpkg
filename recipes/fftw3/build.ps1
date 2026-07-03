# recipes/fftw3/build.ps1
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DBUILD_TESTS=OFF',
    '-DENABLE_THREADS=ON',
    '-DWITH_COMBINED_THREADS=ON'
)

# ── Rewrite .pc for relocatability ──
# FFTW's CMake install bakes the absolute build-time prefix into
# fftw3*.pc; downstream consumers need paths anchored at pcfiledir so
# the package works from any install location.
Get-ChildItem -Path (Join-Path $env:CVC_INSTALL_DIR 'lib/pkgconfig') -Filter 'fftw3*.pc' -ErrorAction SilentlyContinue | ForEach-Object {
    $pc = $_.FullName
    $text = Get-Content -Raw -LiteralPath $pc
    $text = $text -replace '(?m)^prefix=.*$',      'prefix=${pcfiledir}/../..'
    $text = $text -replace '(?m)^exec_prefix=.*$', 'exec_prefix=${prefix}'
    $text = $text -replace '(?m)^libdir=.*$',      'libdir=${prefix}/lib'
    $text = $text -replace '(?m)^includedir=.*$',  'includedir=${prefix}/include'
    Set-Content -LiteralPath $pc -Value $text -NoNewline
}
