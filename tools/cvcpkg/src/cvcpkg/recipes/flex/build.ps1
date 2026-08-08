# recipes/flex/build.ps1 — install pre-built flex on Windows.
#
# On Windows, flex is obtained from winflexbison.
# The bison recipe already installs win_flex.exe as flex.exe, so
# this script checks for that and falls back to downloading the
# winflexbison release if needed.
$ErrorActionPreference = 'Stop'

$winBisonVer = '2.5.25'
$url = "https://github.com/lexxmark/winflexbison/releases/download/v${winBisonVer}/win_flex_bison-${winBisonVer}.zip"
$zipPath = Join-Path $env:CVC_BUILD_DIR "win_flex_bison.zip"

Write-Host "Downloading win_flex_bison ${winBisonVer}..."
Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
Expand-Archive -Path $zipPath -DestinationPath $env:CVC_BUILD_DIR -Force

New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\bin" | Out-Null
Copy-Item "$env:CVC_BUILD_DIR\win_flex.exe" "$env:CVC_INSTALL_DIR\bin\flex.exe"

# Copy FlexLexer.h if present.
if (Test-Path "$env:CVC_BUILD_DIR\FlexLexer.h") {
    New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\include" | Out-Null
    Copy-Item "$env:CVC_BUILD_DIR\FlexLexer.h" "$env:CVC_INSTALL_DIR\include\FlexLexer.h"
}

Write-Host "flex (win_flex_bison ${winBisonVer}) staged to $env:CVC_INSTALL_DIR"
