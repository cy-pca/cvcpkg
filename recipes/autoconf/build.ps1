# recipes/autoconf/build.ps1 — provide GNU Autoconf on Windows via MSYS2/MinGW.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash = Get-CvcGitBash
$env:MSYSTEM = 'MINGW64'

& $bash -lc 'pacman --noconfirm -S --needed autoconf'
if ($LASTEXITCODE -ne 0) { throw 'pacman install of autoconf failed' }

$binDir = Join-Path $env:CVC_INSTALL_DIR 'bin'
if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir | Out-Null }

Set-Content -Path (Join-Path $binDir 'autoconf.cmd') `
    -Value "@echo off`r`n`"$bash`" -lc `"autoconf %*`"`r`n" -NoNewline

Write-Host "autoconf shim written to $binDir\autoconf.cmd"
& $bash -lc 'autoconf --version' | Select-Object -First 1
