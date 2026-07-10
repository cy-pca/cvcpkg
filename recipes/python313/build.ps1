#!/usr/bin/env pwsh
# recipes/python313/build.ps1 — build CPython 3.13 on Windows (MSVC).
#
# CPython on Windows is built with MSVC via its own PCbuild\build.bat, which
# locates Visual Studio itself and — with -e — fetches the prebuilt external
# dependencies (OpenSSL, SQLite, bzip2, xz/liblzma, libffi, tcl/tk). We then
# stage a relocatable install layout with CPython's PC\layout tool.
#
# Note: Windows CPython is a self-contained tree rooted at CVC_INSTALL_DIR
# (python.exe, Lib\, DLLs\, python313.dll, Scripts\pip.exe) — there is no
# python3.exe/pip3; the command is `python`. The Unix versioned-side-by-side
# + python3 meta model does not apply here.
$ErrorActionPreference = 'Stop'
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SCRIPT_DIR "../_common/env-windows.ps1")

Set-Location $env:CVC_SOURCE_DIR

# 1. Fetch externals (-e) and compile x64 Release.
& .\PCbuild\build.bat -e -p x64 -c Release
if ($LASTEXITCODE -ne 0) { throw "PCbuild\build.bat failed ($LASTEXITCODE)" }

# 2. Stage a relocatable layout into the install prefix.
$py = Join-Path (Get-Location) "PCbuild\amd64\python.exe"
& $py PC\layout --copy $env:CVC_INSTALL_DIR --include-pip --include-dev --precompile
if ($LASTEXITCODE -ne 0) { throw "PC\layout failed ($LASTEXITCODE)" }

# 3. Smoke check the staged interpreter.
$staged = Join-Path $env:CVC_INSTALL_DIR "python.exe"
& $staged -c "import ssl, sqlite3, ctypes, lzma, bz2, zlib; import sys; print('cvcpkg: python ' + sys.version.split()[0] + ' ok')"
if ($LASTEXITCODE -ne 0) { throw "staged python smoke check failed ($LASTEXITCODE)" }
