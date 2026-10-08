# recipes/python-pptx-cp311/build.ps1 — build python-pptx 1.0.2 FROM SOURCE
# (import name `pptx`; Windows counterpart of build.sh, same contract).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"

$py = Get-CvcPythonExe
Write-Output "python-pptx-cp311: building with $py"

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
if ($LASTEXITCODE -ne 0) { throw "python-pptx-cp311: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "python-pptx-cp311: no wheel produced under $wheelhouse" }
Write-Output "python-pptx-cp311: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "python-pptx-cp311: pip install failed ($LASTEXITCODE)" }

# The check must exercise the runtime closure: `import pptx` pulls in lxml /
# typing-extensions (staged by the depends graph), and Presentation().save() is
# exactly the off-catalog pip path this recipe replaces.
$env:PYTHONPATH = ''
Invoke-CvcPythonCheck @'
import io, pptx
p = pptx.Presentation()
p.slides.add_slide(p.slide_layouts[6])
buf = io.BytesIO()
p.save(buf)
data = buf.getvalue()
assert data[:2] == b"PK", "pptx (zip) magic missing"
print("python-pptx", pptx.__version__, "wrote a", len(data), "byte deck")
'@
