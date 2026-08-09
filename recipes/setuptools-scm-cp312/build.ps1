# recipes/setuptools-scm-cp312/build.ps1 — build setuptools_scm 8.3.1 FROM
# SOURCE (sdist) for the cp312 column, on Windows.
#
# The recipe used to declare `platform: any` with build.sh alone, on the
# reasoning that a pure-Python package compiles nothing so one script serves
# every platform. That holds for the ARTIFACT and not for the BUILD: build.sh
# resolves the interpreter through cvc_python_exe, which looks for
# <prefix>/bin/python3.12, and a Windows prefix has python.exe at its root with
# site-packages under Lib\. On Windows it fails before doing any work:
#
#   cvc_python_exe_for: interpreter not found: <prefix>/bin/python3.12
#
# This is the same gap #475 closed for the generated pure columns by giving
# each one a windows entry; this recipe is hand-written, so the generator's
# sweep deliberately left it alone (is_generator_owned is false without the
# marker) and it kept the POSIX-only assumption.
#
# See build.sh for the substance — why 8.3.1 rather than 10.x, and the
# self-hosting quirk where the in-tree backend imports the setuptools_scm being
# built (which is why `packaging` is a BUILD dep and not only a runtime one).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"

$py = Get-CvcPythonExe
Write-Output "setuptools-scm-cp312: building with $py"

# Bridge the build-only PEP-517 backend (depends.build -> CVC_BUILD_PREFIX) onto
# the interpreter's import path; --no-build-isolation cannot fetch it. This is
# also what lets the prefix's setuptools win over whatever the base interpreter
# bundles, because PYTHONPATH precedes site-packages in sys.path.
if ($env:CVC_BUILD_PREFIX) {
    $sp = Join-Path $env:CVC_BUILD_PREFIX 'Lib\site-packages'
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$sp;$env:PYTHONPATH" } else { $sp }
}

& $py -c 'import setuptools, packaging; print("setuptools-scm-cp312: setuptools", setuptools.__version__, "packaging", packaging.__version__)'
if ($LASTEXITCODE -ne 0) { throw 'setuptools-scm-cp312: setuptools/packaging not importable from the prefix' }

$root = if ($env:CVC_BUILD_DIR) { $env:CVC_BUILD_DIR } else { $env:CVC_SOURCE_DIR }
$wheelhouse = Join-Path $root 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "setuptools-scm-cp312: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "setuptools-scm-cp312: no wheel produced under $wheelhouse" }
Write-Output "setuptools-scm-cp312: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "setuptools-scm-cp312: pip install failed ($LASTEXITCODE)" }

# The check must exercise the runtime closure, not the build-only backend.
# packaging is a real import-time edge (setuptools_scm._version_cls), so prove
# it resolves from the prefix rather than only happening to be on the builder.
$env:PYTHONPATH = ''
Invoke-CvcPythonCheck @'
import setuptools_scm, packaging
from setuptools_scm import Version
assert Version("1.2.3") < Version("1.10.0")
print("setuptools_scm ->", setuptools_scm.__file__)
print("packaging      :", packaging.__file__)
'@
