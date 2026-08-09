# recipes/ninja/build.ps1 — install pre-built Ninja on Windows.
$ErrorActionPreference = 'Stop'

$ninjaVer = '1.12.1'
$url = "https://github.com/ninja-build/ninja/releases/download/v${ninjaVer}/ninja-win.zip"
$zipPath = Join-Path $env:CVC_BUILD_DIR "ninja.zip"

Write-Host "Downloading Ninja ${ninjaVer} pre-built binary..."
Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
Expand-Archive -Path $zipPath -DestinationPath $env:CVC_BUILD_DIR -Force

New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\bin" | Out-Null
Copy-Item "$env:CVC_BUILD_DIR\ninja.exe" "$env:CVC_INSTALL_DIR\bin\ninja.exe"

Write-Host "Ninja ${ninjaVer} staged to $env:CVC_INSTALL_DIR"
