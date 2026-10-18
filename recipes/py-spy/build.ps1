# recipes/py-spy/build.ps1 — Windows counterpart of build.sh: copy py-spy.exe
# and its license texts out of the pinned wheel (sha256-verified by cvcpkg,
# source.type python_wheel), unpacked with the build interpreter's zipfile.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"

$ver = '0.4.2'
$wheel = Get-ChildItem -Path $env:CVC_SOURCE_DIR -Filter "py_spy-$ver-*.whl" -File |
         Select-Object -First 1
if (-not $wheel) { throw "no py_spy-$ver wheel in $env:CVC_SOURCE_DIR" }

$unpacked = Join-Path $env:CVC_BUILD_DIR 'wheel'
if (Test-Path $unpacked) { Remove-Item -Recurse -Force $unpacked }
$py = Get-CvcPythonExe
& $py -I -m zipfile -e $wheel.FullName $unpacked
if ($LASTEXITCODE -ne 0) { throw "unpacking $($wheel.Name) failed ($LASTEXITCODE)" }

$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
$lic = Join-Path $env:CVC_INSTALL_DIR 'share\licenses\py-spy'
New-Item -ItemType Directory -Force -Path $bin, $lic | Out-Null
Copy-Item (Join-Path $unpacked "py_spy-$ver.data\scripts\py-spy.exe") (Join-Path $bin 'py-spy.exe')
# See build.sh: the SBOM lists the statically linked Rust crates' licenses.
Copy-Item (Join-Path $unpacked "py_spy-$ver.dist-info\licenses\LICENSE") $lic
Copy-Item (Join-Path $unpacked "py_spy-$ver.dist-info\sboms\py-spy.cyclonedx.json") $lic

& (Join-Path $bin 'py-spy.exe') --version
if ($LASTEXITCODE -ne 0) { throw "py-spy --version failed ($LASTEXITCODE)" }
