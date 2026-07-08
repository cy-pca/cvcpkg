#!/usr/bin/env pwsh
# recipes/python313t/build.ps1 — build CPython 3.13 free-threaded on Windows.
#
# CPython 3.13 PCbuild\build.bat accepts --disable-gil to produce the
# free-threaded interpreter: python3.13t.exe / python313t.dll.
# The resulting files use the "t" ABI suffix throughout.
$ErrorActionPreference = 'Stop'
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SCRIPT_DIR "../_common/env-windows.ps1")

$env:PYTHON_VERSION = "3.13.3"
$env:PYTHON_MINOR = "3.13"
$env:CVC_PLATFORM = "windows"

Set-Location $env:CVC_SOURCE_DIR

python ./PCbuild\build.bat `
    --no-tkinter `
    --pgo `
    --disable-gil `
    --experimental-instdir=inst

Copy-Item -Recurse -Force (Join-Path (Get-Location) "inst") $env:CVC_INSTALL_DIR
