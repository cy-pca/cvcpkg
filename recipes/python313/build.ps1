#!/usr/bin/env pwsh
# recipes/python313/build.ps1 — build CPython 3.13 on Windows.
$ErrorActionPreference = 'Stop'
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SCRIPT_DIR "../_common/env-windows.ps1")

$env:PYTHON_VERSION = "3.13.3"
$env:PYTHON_MINOR = "3.13"
$env:CVC_PLATFORM = "windows"

# Use MSVC via python's setup.py
Set-Location $env:CVC_SOURCE_DIR

python ./PCbuild\build.bat `
    --no-tkinter `
    --pgo `
    --experimental-instdir=inst

Copy-Item -Recurse -Force (Join-Path (Get-Location) "inst") $env:CVC_INSTALL_DIR
