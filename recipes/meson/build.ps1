# recipes/meson/build.ps1 — install Meson into the prefix on Windows.
$ErrorActionPreference = 'Stop'

$mesonLib = Join-Path $env:CVC_INSTALL_DIR "lib\meson"
New-Item -ItemType Directory -Force -Path "$env:CVC_INSTALL_DIR\bin" | Out-Null
New-Item -ItemType Directory -Force -Path $mesonLib | Out-Null

# Copy the meson package and entry point.
Copy-Item -Recurse "$env:CVC_SOURCE_DIR\mesonbuild" "$mesonLib\mesonbuild" -Force
Copy-Item "$env:CVC_SOURCE_DIR\meson.py" "$mesonLib\meson.py"

# Create a wrapper batch file.
#
# It must NOT call `python3`. Windows CPython installs python.exe only — there
# is no python3.exe — so `python3` fell through to the Microsoft Store alias
# stub and every meson-based windows recipe died before configuring with
#   Python was not found; run without arguments to install from the Microsoft
#   Store, or disable this shortcut from Settings > Apps ...
# which reads like a missing interpreter rather than a bad launcher.
#
# Prefer the interpreter that ships in THIS prefix (bin\..\python.exe): meson
# then runs on the hermetic python instead of whatever happens to be on PATH.
# Fall back to `python` for prefixes without one (meson is also installed as a
# host tool next to a system interpreter).
$wrapper = @"
@echo off
set "_CVC_PY=%~dp0\..\python.exe"
if exist "%_CVC_PY%" (
  "%_CVC_PY%" "%~dp0\..\lib\meson\meson.py" %*
) else (
  python "%~dp0\..\lib\meson\meson.py" %*
)
"@
Set-Content -Path "$env:CVC_INSTALL_DIR\bin\meson.cmd" -Value $wrapper

Write-Host "meson installed to $env:CVC_INSTALL_DIR"
