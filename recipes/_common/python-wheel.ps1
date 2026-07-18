# recipes/_common/python-wheel.ps1 — per-interpreter wheel helper (Phase 7).
#
# Windows counterpart of python-wheel.sh.  See that file for the rationale;
# the contract is identical:
#
#     . "$PSScriptRoot\..\_common\python-wheel.ps1"
#     Invoke-CvcPipInstallWheel
#     Invoke-CvcPythonCheck 'import numpy; numpy.add(1, 2)'
#
# The wheel is fetched and sha256-verified by cvcpkg core before this runs.

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Resolve the target interpreter inside $env:CVC_DEPS_PREFIX.
#
# Windows CPython installs as python.exe with no version suffix, so the
# free-threaded build is disambiguated by its own prefixed layout: cvcpkg's
# python313t recipe installs python3.13t.exe alongside it.
function Get-CvcPythonExe {
    if (-not $env:CVC_DEPS_PREFIX) { throw 'CVC_DEPS_PREFIX must be set' }
    if (-not $env:CVC_PYTHON_ABI)  { throw 'CVC_PYTHON_ABI must be set (recipe python.abi)' }

    $digits = $env:CVC_PYTHON_ABI -replace '^cp', ''    # cp313t -> 313t
    $suffix = ''
    if ($digits.EndsWith('t')) { $suffix = 't'; $digits = $digits.TrimEnd('t') }
    $ver = "$($digits.Substring(0,1)).$($digits.Substring(1))"   # 313 -> 3.13

    $candidates = @(
        (Join-Path $env:CVC_DEPS_PREFIX "bin\python$ver$suffix.exe"),
        (Join-Path $env:CVC_DEPS_PREFIX "python$ver$suffix.exe")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    throw ("Get-CvcPythonExe: interpreter not found; looked for:`n  " + ($candidates -join "`n  ") +
           "`n  (does this recipe depend on $($env:CVC_PYTHON_INTERPRETER)?)")
}

function Test-CvcPythonFreeThreaded {
    return $env:CVC_PYTHON_ABI.EndsWith('t')
}

# Install the verified wheel into the prefix interpreter's site-packages.
# --no-deps / --no-index: see python-wheel.sh.
function Invoke-CvcPipInstallWheel {
    if (-not $env:CVC_SOURCE_DIR)  { throw 'CVC_SOURCE_DIR must be set' }
    if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }

    $py = Get-CvcPythonExe
    $wheel = Get-ChildItem -Path $env:CVC_SOURCE_DIR -Filter '*.whl' -File |
             Select-Object -First 1
    if (-not $wheel) { throw "Invoke-CvcPipInstallWheel: no .whl in $env:CVC_SOURCE_DIR" }

    Write-Output "installing $($wheel.Name) into $env:CVC_INSTALL_DIR using $py"
    & $py -m pip install --no-deps --no-index --no-compile `
        --prefix $env:CVC_INSTALL_DIR $wheel.FullName
    if ($LASTEXITCODE -ne 0) { throw "pip install failed ($LASTEXITCODE)" }
}

# Run a snippet under the target interpreter with the staged wheel importable.
# For a free-threaded ABI this asserts the GIL is genuinely disabled first —
# see python-wheel.sh for why that assertion is the whole point.
function Invoke-CvcPythonCheck {
    param([Parameter(Mandatory = $true)][string]$Snippet)

    $py = Get-CvcPythonExe
    $libdir = Get-ChildItem -Path $env:CVC_INSTALL_DIR -Filter 'site-packages' `
                            -Recurse -Directory -Depth 3 -ErrorAction SilentlyContinue |
              Select-Object -First 1
    if (-not $libdir) { throw "Invoke-CvcPythonCheck: no site-packages under $env:CVC_INSTALL_DIR" }
    $env:PYTHONPATH = if ($env:PYTHONPATH) { "$($libdir.FullName);$env:PYTHONPATH" }
                      else { $libdir.FullName }

    if (Test-CvcPythonFreeThreaded) {
        Write-Output "verifying $env:CVC_PYTHON_ABI with the GIL disabled"
        $env:PYTHON_GIL = '0'
        $code = @"
import sys, sysconfig
if not sysconfig.get_config_var('Py_GIL_DISABLED'):
    sys.exit('$env:CVC_PYTHON_ABI: interpreter is not a free-threaded build')
if sys._is_gil_enabled():
    sys.exit('$env:CVC_PYTHON_ABI: GIL was re-enabled at runtime; no-GIL support unproven')
print('GIL disabled:', not sys._is_gil_enabled())
$Snippet
print('$env:CVC_PYTHON_ABI check OK (GIL disabled)')
"@
        & $py -X gil=0 -c $code
    } else {
        $code = @"
$Snippet
print('$env:CVC_PYTHON_ABI check OK')
"@
        & $py -c $code
    }
    if ($LASTEXITCODE -ne 0) { throw "python check failed ($LASTEXITCODE)" }
}
