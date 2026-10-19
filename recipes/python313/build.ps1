#!/usr/bin/env pwsh
# recipes/python313/build.ps1 — build CPython 3.13 on Windows (MSVC).
#
# The whole build is _common/build-python.ps1: PCbuild against the cvcpkg
# openssl/zlib/bzip2/xz/sqlite/libffi deps, nothing downloaded, then PC\layout
# into a python.org-style tree at the prefix root (python.exe, python313.dll,
# Lib\, DLLs\, include\, libs\).
$ErrorActionPreference = 'Stop'
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SCRIPT_DIR "../_common/build-python.ps1")

Invoke-CvcPythonBuild
