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

# Drop MinGW/MSYS2 from PATH for the duration of this build.
#
# Same hazard env-windows.ps1's Invoke-CvcCMakeBuild already guards for CMake,
# but a python sdist build never goes near that helper, so it was unprotected:
# setuptools compiles C extensions with cl.exe, and with C:\msys64\mingw64\bin
# on PATH the MinGW-w64 headers win over the MSVC/SDK ones. The result is
# thousands of parse errors in headers the recipe never asked for --
#   C:\msys64\mingw64\include\corecrt.h(159): error C2146: missing ')' ...
#   C:\msys64\mingw64\include\stdio.h(214): error C2061: identifier '__asm__'
# -- which reads like a broken package rather than a polluted toolchain.
# An MSVC build has zero use for anything under msys, so drop it outright.
function Remove-CvcMinGWFromPath {
    $kept = $env:PATH -split ';' | Where-Object {
        $_ -notmatch '(?i)\\msys64\\' -and $_ -notmatch '(?i)\\msys32\\'
    }
    $env:PATH = ($kept -join ';')
}

Remove-CvcMinGWFromPath

# Trim PATH to essentials so vcvarsall/cl invocations don't overflow cmd's limit.
#
# setuptools/distutils compile C extensions by RUNNING vcvarsall.bat and
# capturing its environment (`cmd /u /c "vcvarsall.bat" x86_amd64 && set`). The
# windows-build runner's PATH is ~7.5 KB (the whole machine PATH dumped into
# GITHUB_PATH), and cmd has an ~8 KB command-line limit, so vcvarsall's
# `set PATH=...;%PATH%` overflows — "Error executing cmd ... vcvarsall.bat ...
# && set" — and the C-extension build fails. (Same root cause python311's build
# hit; see recipes/python311/build.ps1.) Rebuild a lean PATH from just the tools
# a python-wheel build needs (system dirs + git/cmake/ninja/pkg-config/nasm/
# python); vcvarsall then extends it with headroom. Capture tool dirs from the
# full PATH before trimming. Runs before Add-CvcPythonToolPaths so the prefix's
# Scripts/bin (short) go back on the now-lean PATH.
function Compress-CvcPathForMsvc {
    $keep = @()
    foreach ($tool in 'git.exe', 'cmake.exe', 'ninja.exe', 'pkg-config.exe', 'nasm.exe', 'python.exe', 'py.exe', 'meson.exe') {
        $c = Get-Command $tool -ErrorAction SilentlyContinue
        if ($c) { $keep += (Split-Path $c.Source) }
    }
    $base = @(
        "$env:SystemRoot\System32",
        "$env:SystemRoot",
        "$env:SystemRoot\System32\Wbem",
        "$env:SystemRoot\System32\WindowsPowerShell\v1.0"
    )
    $env:PATH = (($base + $keep) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique) -join ';'
    Write-Output "python-wheel: trimmed PATH to $($env:PATH.Length) chars for MSVC headroom"
}

Compress-CvcPathForMsvc

# Restore the MSVC toolchain env AFTER trimming PATH.
#
# Compress-CvcPathForMsvc drops the MSVC dirs msvc-dev-cmd put on PATH. That is
# fine for setuptools (it re-runs vcvarsall itself), but meson probes for a C
# compiler at configure time and needs cl.exe + INCLUDE/LIB already present — it
# tries cc/gcc/clang/clang-cl/pgcc and dies with
#   Running `cc --version` gave "[WinError 2] The system cannot find the file specified"
# (it never even reaches cl). Source vcvars64 into the process via a temp .cmd
# (robust quoting; same technique as env-windows.ps1's Import-CvcMsvcEnv). The
# lean already gave the PATH headroom, so vcvars' additions stay well under cmd's
# ~8 KB limit. Harmless for setuptools builds. No-op off a VS-equipped runner.
function Import-CvcVcvarsForWheel {
    $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path $vswhere)) { $vswhere = Join-Path $env:ProgramFiles 'Microsoft Visual Studio\Installer\vswhere.exe' }
    if (-not (Test-Path $vswhere)) { return }
    $vsRoot = & $vswhere -latest -products '*' `
        -requires 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64' `
        -property installationPath 2>$null | Select-Object -First 1
    if (-not $vsRoot) { return }
    $vcvars = Join-Path $vsRoot 'VC\Auxiliary\Build\vcvars64.bat'
    if (-not (Test-Path $vcvars)) { return }
    $helper = Join-Path ([System.IO.Path]::GetTempPath()) ("cvc-vcvars-wheel-{0}.cmd" -f ([guid]::NewGuid().ToString('N')))
    Set-Content -LiteralPath $helper -Value ("@echo off`r`ncall `"$vcvars`" >nul 2>&1`r`nset") -Encoding Ascii
    try { $dump = & cmd.exe /c $helper } finally { Remove-Item -LiteralPath $helper -ErrorAction SilentlyContinue }
    foreach ($line in $dump) {
        if ($line -match '^([^=]+)=(.*)$') {
            $n = $matches[1]; $v = $matches[2]
            if ($n -in @('_', 'PROMPT')) { continue }
            [Environment]::SetEnvironmentVariable($n, $v, 'Process')
        }
    }
}

Import-CvcVcvarsForWheel

# Make the prefixes' console scripts and pkg-config metadata discoverable.
#
# A meson/setuptools sdist build finds its tooling by RUNNING it off PATH, not
# by importing it: meson probes Cython by executing `cython`, and pybind11 by
# executing `pybind11-config`. On Windows those land in <prefix>\Scripts, which
# nothing else puts on PATH, so a build with Cython and pybind11 both installed
# still fails with
#   ERROR: Unknown compiler(s): [['cython'], ['cython3']]
#   Run-time dependency pybind11 found: NO (tried pkg-config, config-tool and cmake)
# — which reads as a missing dependency rather than a missing PATH entry.
# numpy's hand-written recipe already does this inline; generated recipes had
# no equivalent, so this covers every one of them at dot-source time.
#
# pybind11 additionally ships its .pc and cmake config INSIDE the package
# (site-packages\pybind11\share\...), not in <prefix>\lib\pkgconfig, so those
# directories have to be named explicitly for the pkg-config and cmake probes.
function Add-CvcPythonToolPaths {
    $prefixes = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX, $env:CVC_INSTALL_DIR) |
        Where-Object { $_ } | Select-Object -Unique

    $pathParts = @()
    $pcParts = @()
    $cmakeParts = @()
    $mesonPy = $null
    foreach ($p in $prefixes) {
        foreach ($sub in 'Scripts', 'bin') {
            $d = Join-Path $p $sub
            if (Test-Path $d) { $pathParts += $d }
        }
        $pc = Join-Path $p 'lib\pkgconfig'
        if (Test-Path $pc) { $pcParts += $pc }
        $share = Join-Path $p 'Lib\site-packages\pybind11\share'
        if (Test-Path $share) {
            $sharePc = Join-Path $share 'pkgconfig'
            if (Test-Path $sharePc) { $pcParts += $sharePc }
            $cmakeParts += $share
        }
        # Point meson-python at meson's .py entry point (build-prefix wins).
        #
        # cvcpkg's meson package ships lib\meson\meson.py + Scripts\meson.cmd,
        # but NO meson.exe. meson-python detects meson by running
        # `subprocess.run(['meson', '--version'])` (shell=False), and on Windows
        # that goes through CreateProcess, which only auto-resolves .exe and
        # ignores PATHEXT -- it cannot launch meson.cmd by bare name. So even
        # with Scripts on PATH, detection fails with
        #   meson-python: error: meson executable "meson" not found
        # meson-python honors the MESON env var, and when its value ends in
        # `.py` it runs it as [sys.executable, meson.py] -- a real interpreter
        # plus a script, which is both launchable and hermetic (the prefix's
        # own python). Set it to meson.py rather than relying on a .cmd/.exe.
        if (-not $mesonPy) {
            $cand = Join-Path $p 'lib\meson\meson.py'
            if (Test-Path $cand) { $mesonPy = $cand }
        }
    }
    if ($mesonPy -and -not $env:MESON) { $env:MESON = $mesonPy }

    if ($pathParts) { $env:PATH = ($pathParts -join ';') + ';' + $env:PATH }
    if ($pcParts) {
        $joined = $pcParts -join ';'
        $env:PKG_CONFIG_PATH = if ($env:PKG_CONFIG_PATH) { "$joined;$env:PKG_CONFIG_PATH" } else { $joined }
    }
    if ($cmakeParts) {
        $joined = $cmakeParts -join ';'
        $env:CMAKE_PREFIX_PATH = if ($env:CMAKE_PREFIX_PATH) { "$joined;$env:CMAKE_PREFIX_PATH" } else { $joined }
    }
}

Add-CvcPythonToolPaths

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
    # PCbuild installs the interpreter as a bare python.exe at the prefix ROOT
    # — no bin/, no version suffix — so none of the candidates above exist for
    # a windows python31X bundle and every cpXX recipe died here with
    # "interpreter not found". Only the free-threaded build carries a
    # distinguishing name (python3.13t.exe), so the bare fallback is for
    # non-'t' columns only, and we confirm the version rather than trusting the
    # filename: a prefix holding 3.12 must not satisfy a cp313 recipe.
    if (-not $Ver.EndsWith('t')) {
        $bare = Join-Path $env:CVC_DEPS_PREFIX 'python.exe'
        if (Test-Path $bare) {
            $got = (& $bare -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>$null)
            if ($LASTEXITCODE -eq 0 -and $got.Trim() -eq $Ver) { return $bare }
        }
        $candidates += $bare
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
