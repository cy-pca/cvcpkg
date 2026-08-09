# recipes/meson/build.ps1 — install Meson into the prefix on Windows.
$ErrorActionPreference = 'Stop'

$mesonLib = Join-Path $env:CVC_INSTALL_DIR "lib\meson"
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\bin" | Out-Null
New-Item -ItemType Directory -Force -Path $mesonLib | Out-Null

# Copy the meson package and entry point.
Copy-Item -Recurse "$env:CVC_SOURCE_DIR\mesonbuild" "$mesonLib\mesonbuild" -Force
Copy-Item "$env:CVC_SOURCE_DIR\meson.py" "$mesonLib\meson.py"

# Create a wrapper batch file.
$wrapper = @"
@echo off
python3 "%~dp0\..\lib\meson\meson.py" %*
"@
Set-Content -Path "$env:CVC_INSTALL_DIR\bin\meson.cmd" -Value $wrapper

Write-Host "meson installed to $env:CVC_INSTALL_DIR"
