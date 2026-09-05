# pocketfft is header-only: nothing compiles, so this just stages the header.
# No env-windows.ps1 / MSVC setup needed -- there is no compiler invocation.
$ErrorActionPreference = 'Stop'

if (-not $env:CVC_SOURCE_DIR)  { throw 'CVC_SOURCE_DIR must be set' }
if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }

$inc = Join-Path $env:CVC_INSTALL_DIR 'include'
New-Item -ItemType Directory -Force -Path $inc | Out-Null
Copy-Item -Force (Join-Path $env:CVC_SOURCE_DIR 'pocketfft_hdronly.h') $inc

$share = Join-Path $env:CVC_INSTALL_DIR 'share\pocketfft'
New-Item -ItemType Directory -Force -Path $share | Out-Null
foreach ($f in @('LICENSE.md','LICENSE','COPYING')) {
    $p = Join-Path $env:CVC_SOURCE_DIR $f
    if (Test-Path $p) { Copy-Item -Force $p $share }
}

Write-Host "cvcpkg: installed pocketfft_hdronly.h -> $inc"
