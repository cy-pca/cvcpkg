#!/usr/bin/env pwsh
# recipes/python312/build.ps1 — build CPython 3.12 on Windows (MSVC).
#
# CPython on Windows is built with MSVC via its own PCbuild\build.bat, which
# locates Visual Studio itself and — with -e — fetches the prebuilt external
# dependencies (OpenSSL, SQLite, bzip2, xz/liblzma, libffi, tcl/tk). We then
# stage a relocatable install layout with CPython's PC\layout tool.
#
# Note: Windows CPython is a self-contained tree rooted at CVC_INSTALL_DIR
# (python.exe, Lib\, DLLs\, python312.dll, Scripts\pip.exe) — there is no
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

# 2b. Teach the interpreter where the prefix's DLLs live.
#
# Windows has no RPATH, and since Python 3.8 the loader no longer searches PATH
# for an extension module's dependencies — only the DLL search directories.
# cvcpkg keeps shared libraries in <prefix>\bin, so every extension that links
# one (pillow -> zlib1/jpeg62/tiff, numpy -> openblas, h5py -> hdf5) imports
# with "DLL load failed while importing _x: The specified module could not be
# found" no matter what PATH says. This is the Windows counterpart of the
# $ORIGIN RUNPATH the POSIX builds patch in.
#
# A .pth is the one hook that runs at interpreter startup before any import.
# The path is derived from the running interpreter's own prefix, so the tree
# stays relocatable.
$siteDir = Join-Path $env:CVC_INSTALL_DIR "Lib\site-packages"
New-Item -ItemType Directory -Force -Path $siteDir | Out-Null
$pth = @'
import os, sys; _b = os.path.join(sys.prefix, 'bin'); os.path.isdir(_b) and hasattr(os, 'add_dll_directory') and os.add_dll_directory(_b)
'@
Set-Content -Path (Join-Path $siteDir "cvcpkg-dll-directories.pth") -Value $pth -Encoding ASCII

# 3. Smoke check the staged interpreter.
$staged = Join-Path $env:CVC_INSTALL_DIR "python.exe"
& $staged -c "import ssl, sqlite3, ctypes, lzma, bz2, zlib; import sys; print('cvcpkg: python ' + sys.version.split()[0] + ' ok')"
if ($LASTEXITCODE -ne 0) { throw "staged python smoke check failed ($LASTEXITCODE)" }
