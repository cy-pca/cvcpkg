# recipes/bison/build.ps1 — build GNU Bison on Windows.
#
# On Windows, bison is typically obtained from win_bison (winflexbison).
# We download the pre-built winflexbison release from GitHub.
$ErrorActionPreference = 'Stop'

$winBisonVer = '2.5.25'
$url = "https://github.com/lexxmark/winflexbison/releases/download/v${winBisonVer}/win_flex_bison-${winBisonVer}.zip"
$zipPath = Join-Path $env:CVC_BUILD_DIR "win_flex_bison.zip"

Write-Host "Downloading win_flex_bison ${winBisonVer}..."
Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
Expand-Archive -Path $zipPath -DestinationPath $env:CVC_BUILD_DIR -Force

# Stage into install prefix.
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\bin" | Out-Null
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\share\bison" | Out-Null

Copy-Item "$env:CVC_BUILD_DIR\win_bison.exe" "$env:CVC_INSTALL_DIR\bin\bison.exe"
Copy-Item "$env:CVC_BUILD_DIR\win_flex.exe"  "$env:CVC_INSTALL_DIR\bin\flex.exe"

# Copy data files if present.
if (Test-Path "$env:CVC_BUILD_DIR\data") {
    Copy-Item -Recurse "$env:CVC_BUILD_DIR\data\*" "$env:CVC_INSTALL_DIR\share\bison\" -Force
}

Write-Host "bison (win_flex_bison ${winBisonVer}) staged to $env:CVC_INSTALL_DIR"
