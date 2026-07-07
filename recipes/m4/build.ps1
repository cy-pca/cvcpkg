# recipes/m4/build.ps1 — provide GNU m4 on Windows via MSYS2/MinGW.
#
# m4 is a build-time host tool only; no Windows library is produced.
# The recipe ensures the MSYS2 m4 package is installed and creates
# shim wrappers in $CVC_INSTALL_DIR\bin so the cvcpkg dependency
# graph resolves correctly.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash = Get-CvcGitBash
$env:MSYSTEM = 'MINGW64'

# Ensure the package is installed (pacman is a no-op if already present).
& $bash -lc 'pacman --noconfirm -S --needed m4'
if ($LASTEXITCODE -ne 0) { throw 'pacman install of m4 failed' }

# Create a shim so other recipes find m4 via $CVC_DEPS_PREFIX/bin/m4.
$binDir  = Join-Path $env:CVC_INSTALL_DIR 'bin'
if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir | Out-Null }

# Locate the actual m4 binary inside MSYS2.
$m4Path = & $bash -lc 'command -v m4'
if ($m4Path) { $m4Path = $m4Path.Trim() }

$shimContent = "@echo off`r`n`"$bash`" -lc `"m4 %*`"`r`n"
Set-Content -Path (Join-Path $binDir 'm4.cmd') -Value $shimContent -NoNewline
Write-Host "m4 shim written to $binDir\m4.cmd"

& $bash -lc 'm4 --version' | Select-Object -First 1
