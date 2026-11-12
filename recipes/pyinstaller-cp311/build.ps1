# recipes/pyinstaller-cp311/build.ps1 — Windows from-source build of PyInstaller 6.21.0.
#
# Mirrors build.sh. The one Windows-specific fact worth knowing: the sdist
# already carries prebuilt Windows bootloaders (PyInstaller/bootloader/
# Windows-{32,64}bit-intel), and the build hook uses them unless told
# otherwise. Those prebuilt stubs are shared by every PyInstaller app, malware
# included, and Microsoft Defender's machine-learned signatures quarantined the
# cvcpkg Windows exe built on them (2.5.0, 2.5.12, 2.5.13 as
# Trojan:Win32/Bearfoos.A!ml; cy-pca/cvcpkg#609). So this build sets
# PYINSTALLER_COMPILE_BOOTLOADER and compiles the bootloader with MSVC
# (env-windows.ps1 puts cl.exe on PATH; waf finds it), and fails if the
# installed run.exe is still the sdist's prebuilt one.
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"
. "$scriptDir\..\_common\python-wheel.ps1"

if (-not $env:CVC_PYTHON_ABI) { $env:CVC_PYTHON_ABI = "cp311" }
$pyExe = Get-CvcPythonExe
Write-Host "pyinstaller-cp311: building against $pyExe"

$wheelhouse = Join-Path $env:CVC_BUILD_DIR "wheelhouse"
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

# Compile the Windows bootloader from source instead of shipping the prebuilt one.
# pip builds this source tree in place, so the compiled run.exe overwrites the
# prebuilt one: note the prebuilt hash first, for the check after install.
$prebuilt = Join-Path $env:CVC_SOURCE_DIR 'PyInstaller\bootloader\Windows-64bit-intel\run.exe'
$prebuiltHash = if (Test-Path $prebuilt) { (Get-FileHash $prebuilt).Hash } else { $null }
$env:PYINSTALLER_COMPILE_BOOTLOADER = '1'
# env-windows.ps1 sets CC=cl and CXX=cl for every recipe. waf's msvc tool finds
# CL.exe on its own, but then takes CC/CXX literally and rejects the bare name
# ("Program ['cl'] is not executable" in bootloader/build/config.log), so the
# bootloader configure fails. Unset them for this build.
Remove-Item Env:CC, Env:CXX -ErrorAction SilentlyContinue

# --no-index/--no-deps keep pip inside cvcpkg's graph; --no-build-isolation
# imports the hatchling backend from the closure rather than fetching it.
& $pyExe -m pip wheel --no-deps --no-build-isolation --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "pyinstaller-cp311: pip wheel failed" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter "pyinstaller-*.whl" | Select-Object -First 1
if (-not $wheel) { throw "pyinstaller-cp311: no wheel produced under $wheelhouse" }
Write-Host "pyinstaller-cp311: built $($wheel.Name)"

& $pyExe -m pip install --no-index --no-deps --no-compile `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "pyinstaller-cp311: pip install failed" }

Invoke-CvcPythonCheck "import PyInstaller; print('PyInstaller', PyInstaller.__version__)"

# The bootloader that landed must be the one compiled here, not the sdist's.
$installed = Get-ChildItem -Path $env:CVC_INSTALL_DIR -Recurse -File -Filter 'run.exe' |
    Where-Object { $_.FullName -match 'PyInstaller\\bootloader\\Windows-64bit-intel' } |
    Select-Object -First 1
if (-not $installed) { throw "pyinstaller-cp311: no Windows-64bit-intel run.exe in the install" }
if ($prebuiltHash -and ($prebuiltHash -eq (Get-FileHash $installed.FullName).Hash)) {
    throw "pyinstaller-cp311: the installed bootloader is the sdist's prebuilt run.exe; it was not compiled"
}
Write-Host "pyinstaller-cp311: bootloader compiled from source ($($installed.FullName))"

Write-Host "pyinstaller-cp311: build + verification complete"
