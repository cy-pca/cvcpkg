#!/usr/bin/env pwsh
# recipes/python313t/build.ps1 — build free-threaded CPython 3.13 on Windows.
#
# python313's build (_common/build-python.ps1) with -FreeThreaded: PCbuild with
# DisableGil=true (build.bat's --disable-gil), then PC\layout
# --include-freethreaded, which stages python3.13t.exe, pythonw3.13t.exe,
# python313t.dll, python3t.dll and the *.cp313t-win_amd64.pyd modules.  The
# interpreter has no bare python.exe: consumers find it as python3.13t.exe
# (_common/python-wheel.ps1).
#
# This used to run `python ./PCbuild\build.bat ... --experimental-instdir`,
# which handed a batch file to python with a flag PCbuild does not have, so
# Windows python313t never built.
$ErrorActionPreference = 'Stop'
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SCRIPT_DIR "../_common/build-python.ps1")

Invoke-CvcPythonBuild -FreeThreaded
