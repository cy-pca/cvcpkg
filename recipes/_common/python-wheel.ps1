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

# Map a cvcpkg interpreter recipe name to the X.Y[t] version it reports.
#   python311 -> 3.11 ; python313t -> 3.13t
# Authoritative even for abi3, whose ABI tag carries no version.
function Get-CvcInterpVersion {
    param([Parameter(Mandatory = $true)][string]$Interp)
    $digits = $Interp -replace '^python', ''    # python313t -> 313t
    $suffix = ''
    if ($digits.EndsWith('t')) { $suffix = 't'; $digits = $digits.TrimEnd('t') }
    return "$($digits.Substring(0,1)).$($digits.Substring(1))$suffix"
}

# Resolve the prefix interpreter exe for an X.Y[t] version.
#
# Windows CPython installs as python.exe with no version suffix, so the
# free-threaded build is disambiguated by its own prefixed layout: cvcpkg's
# python313t recipe installs python3.13t.exe alongside it.
function Get-CvcPythonExeFor {
    param([Parameter(Mandatory = $true)][string]$Ver)
    if (-not $env:CVC_DEPS_PREFIX) { throw 'CVC_DEPS_PREFIX must be set' }
    $candidates = @(
        (Join-Path $env:CVC_DEPS_PREFIX "bin\python$Ver.exe"),
        (Join-Path $env:CVC_DEPS_PREFIX "python$Ver.exe")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    throw ("Get-CvcPythonExeFor: interpreter not found; looked for:`n  " + ($candidates -join "`n  ") +
           "`n  (does this recipe depend on python$($Ver -replace '\.', ''))?")
}

# The interpreter a wheel is installed *under* is the recipe's interpreter,
# not the ABI tag (which is `abi3`, versionless, for a stable-ABI recipe).
function Get-CvcPythonExe {
    if (-not $env:CVC_PYTHON_INTERPRETER) { throw 'CVC_PYTHON_INTERPRETER must be set (recipe python.interpreter)' }
    return Get-CvcPythonExeFor (Get-CvcInterpVersion $env:CVC_PYTHON_INTERPRETER)
}

function Test-CvcPythonFreeThreaded {
    return $env:CVC_PYTHON_ABI.EndsWith('t')
}

# Echo the X.Y[t] version a wheel's ABI tag targets, from its filename.
#   foo-1.0-cp311-cp311-...whl -> 3.11 ; foo-1.0-cp313-cp313t-...whl -> 3.13t
function Get-CvcWheelAbiVersion {
    param([Parameter(Mandatory = $true)][string]$WheelName)
    $tags = [regex]::Matches($WheelName, 'cp3[0-9]{2}t?')
    if ($tags.Count -eq 0) { throw "Get-CvcWheelAbiVersion: no cpNN tag in $WheelName" }
    $abi = $tags[$tags.Count - 1].Value   # last match: abitag (trailing `t` wins)
    $digits = $abi -replace '^cp', ''
    $suffix = ''
    if ($digits.EndsWith('t')) { $suffix = 't'; $digits = $digits.TrimEnd('t') }
    return "$($digits.Substring(0,1)).$($digits.Substring(1))$suffix"
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
    & $py -m pip install --no-deps --no-index --no-compile --ignore-installed `
        --prefix $env:CVC_INSTALL_DIR $wheel.FullName
    if ($LASTEXITCODE -ne 0) { throw "pip install failed ($LASTEXITCODE)" }

    Invoke-CvcNoarchFanout
}

# Cross-interpreter copy fan-out for a noarch (py3-none-any) or stable-ABI
# (abi3) install — one payload valid on every listed interpreter. See
# cvc_noarch_fanout in python-wheel.sh. A no-op for a true per-version
# extension, where CVC_PYTHON_NOARCH_FANOUT is unset.
function Invoke-CvcNoarchFanout {
    if (-not $env:CVC_PYTHON_NOARCH_FANOUT) { return }
    if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }

    $srcSp = Get-ChildItem -Path $env:CVC_INSTALL_DIR -Filter 'site-packages' `
                           -Recurse -Directory -Depth 3 -ErrorAction SilentlyContinue |
             Select-Object -First 1
    if (-not $srcSp) { throw "Invoke-CvcNoarchFanout: no site-packages under $env:CVC_INSTALL_DIR" }
    foreach ($v in ($env:CVC_PYTHON_NOARCH_FANOUT -split '\s+' | Where-Object { $_ })) {
        $dst = Join-Path $env:CVC_INSTALL_DIR "lib\python$v\site-packages"
        if ($dst -eq $srcSp.FullName) { continue }
        New-Item -ItemType Directory -Force -Path $dst | Out-Null
        Copy-Item -Path (Join-Path $srcSp.FullName '*') -Destination $dst -Recurse -Force
    }
    Write-Output "noarch fan-out: staged into python $env:CVC_PYTHON_NOARCH_FANOUT"
}

# Per-version binary fan-out: install each pinned wheel present in
# CVC_SOURCE_DIR into the interpreter matching its own ABI tag. See
# cvc_pip_install_wheels_fanout in python-wheel.sh.
function Invoke-CvcPipInstallWheelsFanout {
    if (-not $env:CVC_SOURCE_DIR)  { throw 'CVC_SOURCE_DIR must be set' }
    if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }

    $wheels = Get-ChildItem -Path $env:CVC_SOURCE_DIR -Filter '*.whl' -File
    if (-not $wheels) { throw "Invoke-CvcPipInstallWheelsFanout: no .whl in $env:CVC_SOURCE_DIR" }
    foreach ($wheel in $wheels) {
        $ver = Get-CvcWheelAbiVersion $wheel.Name
        $py  = Get-CvcPythonExeFor $ver
        Write-Output "installing $($wheel.Name) into $env:CVC_INSTALL_DIR using $py"
        & $py -m pip install --no-deps --no-index --no-compile --ignore-installed `
            --prefix $env:CVC_INSTALL_DIR $wheel.FullName
        if ($LASTEXITCODE -ne 0) { throw "pip install failed for $($wheel.Name) ($LASTEXITCODE)" }
    }
    Write-Output "per-version fan-out: installed $($wheels.Count) wheel(s)"
}

# Run a snippet under EVERY interpreter a per-version fan-out installed into,
# each with only its own staged site-packages importable.
function Invoke-CvcPythonCheckEach {
    param([Parameter(Mandatory = $true)][string]$Snippet)
    if (-not $env:CVC_SOURCE_DIR)  { throw 'CVC_SOURCE_DIR must be set' }
    if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }

    foreach ($wheel in (Get-ChildItem -Path $env:CVC_SOURCE_DIR -Filter '*.whl' -File)) {
        $ver = Get-CvcWheelAbiVersion $wheel.Name
        $py  = Get-CvcPythonExeFor $ver
        $sp  = Join-Path $env:CVC_INSTALL_DIR "lib\python$ver\site-packages"
        if (-not (Test-Path $sp)) {
            $sp = Join-Path $env:CVC_INSTALL_DIR 'Lib\site-packages'
        }
        Write-Output "verifying cp$($ver -replace '\.', '') under $py"
        $env:PYTHONPATH = $sp
        & $py -c @"
$Snippet
print('cp$($ver -replace '\.', '') check OK')
"@
        if ($LASTEXITCODE -ne 0) { throw "python check failed for python$ver ($LASTEXITCODE)" }
    }
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
