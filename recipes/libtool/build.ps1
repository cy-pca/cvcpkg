# recipes/libtool/build.ps1 — provide GNU Libtool on Windows via MSYS2/MinGW.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash = Get-CvcGitBash
$env:MSYSTEM = 'MINGW64'

& $bash -lc 'pacman --noconfirm -S --needed libtool'
if ($LASTEXITCODE -ne 0) { throw 'pacman install of libtool failed' }

$binDir = Join-Path $env:CVC_INSTALL_DIR 'bin'
if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir | Out-Null }

Set-Content -Path (Join-Path $binDir 'libtool.cmd') `
    -Value "@echo off`r`n`"$bash`" -lc `"libtool %*`"`r`n" -NoNewline
Set-Content -Path (Join-Path $binDir 'libtoolize.cmd') `
    -Value "@echo off`r`n`"$bash`" -lc `"libtoolize %*`"`r`n" -NoNewline

Write-Host "libtool shim written to $binDir\libtool.cmd"
& $bash -lc 'libtool --version' | Select-Object -First 1
