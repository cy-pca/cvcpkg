# recipes/nasm/build.ps1 — install the prebuilt NASM binary on Windows.
#
# NASM provides official Win64 zip releases; no compilation required.
$ErrorActionPreference = 'Stop'

$nasmVer = '2.16.03'
$url     = "https://www.nasm.us/pub/nasm/releasebuilds/$nasmVer/win64/nasm-$nasmVer-win64.zip"
$zipPath = Join-Path $env:CVC_BUILD_DIR "nasm-$nasmVer-win64.zip"

Write-Host "Downloading NASM $nasmVer ..."
Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
Expand-Archive -Path $zipPath -DestinationPath $env:CVC_BUILD_DIR -Force

$binDir = Join-Path $env:CVC_INSTALL_DIR 'bin'
if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir | Out-Null }

$src = Join-Path $env:CVC_BUILD_DIR "nasm-$nasmVer"
Copy-Item (Join-Path $src 'nasm.exe')    (Join-Path $binDir 'nasm.exe')    -Force
Copy-Item (Join-Path $src 'ndisasm.exe') (Join-Path $binDir 'ndisasm.exe') -Force

Write-Host "NASM $nasmVer installed to $env:CVC_INSTALL_DIR"
& (Join-Path $binDir 'nasm.exe') --version
