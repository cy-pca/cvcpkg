# recipes/pyinstaller-cp311/build.ps1 — Windows from-source build of PyInstaller 6.21.0.
#
# Mirrors build.sh. The one Windows-specific fact worth knowing: the sdist
# already carries prebuilt Windows bootloaders (PyInstaller/bootloader/
# Windows-{32,64}bit-intel), so this path does NOT invoke a C compiler — the
# build hook only compiles a bootloader for platforms the sdist has no binary
# for (linux and the BSDs). That keeps this script a pip wheel + pip install,
# with no MSVC dependency.
$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"
. "$scriptDir\..\_common\python-wheel.ps1"

if (-not $env:CVC_PYTHON_ABI) { $env:CVC_PYTHON_ABI = "cp311" }
$pyExe = Get-CvcPythonExe
Write-Host "pyinstaller-cp311: building against $pyExe"

$wheelhouse = Join-Path $env:CVC_BUILD_DIR "wheelhouse"
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null

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

Write-Host "pyinstaller-cp311: build + verification complete"
