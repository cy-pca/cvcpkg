# recipes/emsdk/build.ps1 — snapshot an activated Emscripten SDK on Windows.
$ErrorActionPreference = 'Stop'

$emsdkVer = '5.0.7'

Set-Location $env:CVC_SOURCE_DIR

# Install and activate the pinned version.
& .\emsdk.bat install $emsdkVer
if ($LASTEXITCODE -ne 0) { throw "emsdk install failed" }

& .\emsdk.bat activate $emsdkVer
if ($LASTEXITCODE -ne 0) { throw "emsdk activate failed" }

# Source the environment for ports pre-build.
& .\emsdk_env.bat

# Pre-populate the Emscripten ports cache.
& embuilder build MINIMAL
if ($LASTEXITCODE -ne 0) { Write-Warning "embuilder MINIMAL failed (non-fatal)" }

# Stage into install prefix — copy the entire activated tree.
$excludeDirs = @('.git', '.github')
Get-ChildItem -Path $env:CVC_SOURCE_DIR -Exclude $excludeDirs |
    Copy-Item -Destination $env:CVC_INSTALL_DIR -Recurse -Force

Write-Host "emsdk $emsdkVer staged to $env:CVC_INSTALL_DIR"
