# recipes/cmake/build.ps1 — install pre-built CMake on Windows.
$ErrorActionPreference = 'Stop'

$cmakeVer = '3.31.7'
$url = "https://github.com/Kitware/CMake/releases/download/v${cmakeVer}/cmake-${cmakeVer}-windows-x86_64.zip"
$zipPath = Join-Path $env:CVC_BUILD_DIR "cmake.zip"

Write-Host "Downloading CMake ${cmakeVer} pre-built binaries..."
Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
Expand-Archive -Path $zipPath -DestinationPath $env:CVC_BUILD_DIR -Force

$extractedDir = Join-Path $env:CVC_BUILD_DIR "cmake-${cmakeVer}-windows-x86_64"

# Stage into install prefix.
Copy-Item -Recurse "$extractedDir\bin"   "$env:CVC_INSTALL_DIR\bin"   -Force
Copy-Item -Recurse "$extractedDir\share" "$env:CVC_INSTALL_DIR\share" -Force

Write-Host "CMake ${cmakeVer} staged to $env:CVC_INSTALL_DIR"
