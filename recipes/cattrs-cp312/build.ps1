# recipes/cattrs-cp312/build.ps1 — build cattrs 25.1.1 FROM SOURCE (generated).
# Windows counterpart of build.sh; same contract (see that file for the why).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
. "$PSScriptRoot\..\_common\python-wheel.ps1"

$py = Get-CvcPythonExe
Write-Output "cattrs-cp312: building with $py"

# Bridge the build-only PEP-517 backend (depends.build -> CVC_BUILD_PREFIX) onto
# the interpreter's import path; --no-build-isolation cannot fetch it.
if ($env:CVC_BUILD_PREFIX) {
    $sp = Join-Path $env:CVC_BUILD_PREFIX 'Lib\site-packages'
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$sp;$env:PYTHONPATH" } else { $sp }
}

$root = if ($env:CVC_BUILD_DIR) { $env:CVC_BUILD_DIR } else { $env:CVC_SOURCE_DIR }
$wheelhouse = Join-Path $root 'wheelhouse'
New-Item -ItemType Directory -Force -Path $wheelhouse | Out-Null
& $py -m pip wheel --no-build-isolation --no-deps --no-index --no-cache-dir `
    --wheel-dir $wheelhouse $env:CVC_SOURCE_DIR
if ($LASTEXITCODE -ne 0) { throw "cattrs-cp312: pip wheel failed ($LASTEXITCODE)" }

$wheel = Get-ChildItem -Path $wheelhouse -Filter '*.whl' -File | Select-Object -First 1
if (-not $wheel) { throw "cattrs-cp312: no wheel produced under $wheelhouse" }
Write-Output "cattrs-cp312: built $($wheel.Name)"

& $py -m pip install --no-index --no-deps --no-compile --ignore-installed `
    --prefix $env:CVC_INSTALL_DIR $wheel.FullName
if ($LASTEXITCODE -ne 0) { throw "cattrs-cp312: pip install failed ($LASTEXITCODE)" }

# The check must exercise the runtime closure, not the build-only backend.
$env:PYTHONPATH = ''
Invoke-CvcPythonCheck 'import unittest, attrs, cattrs; from cattrs.preconf.json import make_converter; P = attrs.make_class(''P'', {''x'': attrs.field(type=int), ''tags'': attrs.field(type=list[str])}); B = attrs.make_class(''B'', {''p'': attrs.field(type=P), ''n'': attrs.field(type=float)}); c = cattrs.Converter(); b = c.structure({''p'': {''x'': ''3'', ''tags'': [''a'']}, ''n'': 1}, B); assert b == B(P(3, [''a'']), 1.0), b; assert c.unstructure(b) == {''p'': {''x'': 3, ''tags'': [''a'']}, ''n'': 1.0}, c.unstructure(b); unittest.TestCase().assertRaises(cattrs.ClassValidationError, c.structure, {''p'': {''x'': ''no'', ''tags'': []}, ''n'': 1}, B); j = make_converter(); assert j.loads(j.dumps(b), B) == b'
