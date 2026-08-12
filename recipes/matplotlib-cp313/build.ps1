# recipes/matplotlib-cp313/build.ps1 — build matplotlib 3.10.0 FROM SOURCE (generated).
# Windows counterpart of build.sh; same contract (see that file for the why).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"

$py = Get-CvcPythonExe
Write-Output "matplotlib-cp313: building with $py"

# Bridge the build-only PEP-517 backend (depends.build -> CVC_BUILD_PREFIX) onto
# the interpreter's import path; --no-build-isolation cannot fetch it.
if ($env:CVC_BUILD_PREFIX) {
    $sp = Join-Path $env:CVC_BUILD_PREFIX 'Lib\site-packages'
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$sp;$env:PYTHONPATH" } else { $sp }
}

# pybind11 needs no per-recipe discovery: Add-CvcPythonToolPaths
# (python-wheel.ps1, dot-sourced above) already puts <prefix>\Scripts on PATH
# for meson's pybind11-config probe.  System freetype/qhull — see build.sh.
# sdist builds have no git metadata; pin the version for setuptools_scm.
$env:SETUPTOOLS_SCM_PRETEND_VERSION_FOR_MATPLOTLIB = '3.10.0'
$env:SETUPTOOLS_SCM_PRETEND_VERSION = '3.10.0'

$root = if ($env:CVC_BUILD_DIR) { $env:CVC_BUILD_DIR } else { $env:CVC_SOURCE_DIR }
$wheelhouse = Join-Path $root 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    -C setup-args=-Dsystem-freetype=true -C setup-args=-Dsystem-qhull=true `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "matplotlib-cp313: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "matplotlib-cp313: no wheel produced under $wheelhouse" }
Write-Output "matplotlib-cp313: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "matplotlib-cp313: pip install failed ($LASTEXITCODE)" }

# The check must exercise the runtime closure, not the build-only backend.
$env:PYTHONPATH = ''
Invoke-CvcPythonCheck 'import matplotlib; matplotlib.use(''Agg''); import matplotlib.pyplot'
