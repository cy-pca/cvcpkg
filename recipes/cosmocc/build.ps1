# recipes/cosmocc/build.ps1 — install the cosmocc toolchain bundle on Windows.
#
# cosmocc.zip is host-agnostic (the same APE binaries run on Linux,
# macOS, Windows, and BSDs).  The tree is fully relocatable, so we
# just copy it under $CVC_INSTALL_DIR\cosmocc\.
$ErrorActionPreference = 'Stop'

if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }
if (-not $env:CVC_SOURCE_DIR)  { throw 'CVC_SOURCE_DIR must be set' }

$dest = Join-Path $env:CVC_INSTALL_DIR 'cosmocc'
New-Item -ItemType Directory -Force -Path $dest | Out-Null

foreach ($item in 'bin','include','lib','libexec','x86_64-linux-cosmo','aarch64-linux-cosmo') {
    $src = Join-Path $env:CVC_SOURCE_DIR $item
    if (Test-Path $src) {
        Copy-Item -Recurse -Force $src $dest
    }
}
foreach ($f in 'LICENSE.gpl2','LICENSE.gpl3','LICENSE.lgpl2','LICENSE.lgpl3','Name','README.md') {
    $src = Join-Path $env:CVC_SOURCE_DIR $f
    if (Test-Path $src) { Copy-Item -Force $src $dest }
}

$cosmocc = Join-Path $dest 'bin\cosmocc'
if (-not (Test-Path $cosmocc)) {
    throw "cosmocc binary not found at $cosmocc after install"
}
Write-Host "cvcpkg: cosmocc toolchain installed at $dest"
