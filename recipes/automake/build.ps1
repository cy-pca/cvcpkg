# recipes/automake/build.ps1 — provide GNU Automake on Windows via MSYS2/MinGW.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$bash = Get-CvcGitBash
$env:MSYSTEM = 'MINGW64'

& $bash -lc 'pacman --noconfirm -S --needed automake'
if ($LASTEXITCODE -ne 0) { throw 'pacman install of automake failed' }

$binDir = Join-Path $env:CVC_INSTALL_DIR 'bin'
if (-not (Test-Path $binDir)) { New-Item -ItemType Directory -Path $binDir | Out-Null }

Set-Content -Path (Join-Path $binDir 'automake.cmd') `
    -Value "@echo off`r`n`"$bash`" -lc `"automake %*`"`r`n" -NoNewline
Set-Content -Path (Join-Path $binDir 'aclocal.cmd') `
    -Value "@echo off`r`n`"$bash`" -lc `"aclocal %*`"`r`n" -NoNewline

Write-Host "automake shim written to $binDir\automake.cmd"
& $bash -lc 'automake --version' | Select-Object -First 1
