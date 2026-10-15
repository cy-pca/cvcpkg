# recipes/xlsxwriter-cp311/build.ps1 — build XlsxWriter 3.2.9 FROM SOURCE
# (generated-shape sdist build; Windows counterpart of build.sh, same contract).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"

$py = Get-CvcPythonExe
Write-Output "xlsxwriter-cp311: building with $py"

# Bridge the build-only backend (setuptools/wheel -> CVC_BUILD_PREFIX) onto the
# interpreter's import path; --no-build-isolation cannot fetch it.
if ($env:CVC_BUILD_PREFIX) {
    $sp = Join-Path $env:CVC_BUILD_PREFIX 'Lib\site-packages'
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$sp;$env:PYTHONPATH" } else { $sp }
}

$root = if ($env:CVC_BUILD_DIR) { $env:CVC_BUILD_DIR } else { $env:CVC_SOURCE_DIR }
$wheelhouse = Join-Path $root 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "xlsxwriter-cp311: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "xlsxwriter-cp311: no wheel produced under $wheelhouse" }
Write-Output "xlsxwriter-cp311: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "xlsxwriter-cp311: pip install failed ($LASTEXITCODE)" }

# The check must exercise the runtime closure, not the build-only backend.
$env:PYTHONPATH = ''
Invoke-CvcPythonCheck @'
import io, xlsxwriter
buf = io.BytesIO()
wb = xlsxwriter.Workbook(buf)
ws = wb.add_worksheet()
ws.write(0, 0, "cvcpkg")
ws.write_number(0, 1, 42)
wb.close()
data = buf.getvalue()
assert data[:2] == b"PK", "xlsx (zip) magic missing"
print("xlsxwriter", xlsxwriter.__version__, "wrote", len(data), "bytes")
'@
