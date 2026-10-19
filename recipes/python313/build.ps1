#!/usr/bin/env pwsh
# recipes/python313/build.ps1 — build CPython 3.13 on Windows (MSVC).
#
# CPython on Windows is built with MSVC via its own PCbuild\build.bat, which
# locates Visual Studio itself and — with -e — fetches the prebuilt external
# dependencies (OpenSSL, SQLite, bzip2, xz/liblzma, libffi, tcl/tk). We then
# stage a relocatable install layout with CPython's PC\layout tool.
#
# Note: Windows CPython is a self-contained tree rooted at CVC_INSTALL_DIR
# (python.exe, Lib\, DLLs\, python313.dll, Scripts\pip.exe) — there is no
# python3.exe/pip3; the command is `python`. The Unix versioned-side-by-side
# + python3 meta model does not apply here.
#
# The PATH-trim and vcvars64 steps below are python311's (its build.ps1 explains
# them): without them MSBuild's _decimal ml64 step fails on a long CI PATH.
$ErrorActionPreference = 'Stop'
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SCRIPT_DIR "../_common/env-windows.ps1")

Set-Location $env:CVC_SOURCE_DIR

# ── Trim PATH to essentials before building ─────────────────────────────────
# The workflow's "Install system build tools" step dumps the ENTIRE machine PATH
# into GITHUB_PATH, bloating PATH to ~7.5 KB. cmd.exe has an ~8 KB command-line
# limit, so when vcvars64 — or MSBuild's _decimal `ml64` custom-build — extends
# PATH via `set PATH=...;%PATH%` it overflows ("The input line is too long"),
# the extension silently fails, and the MASM assembler dir never lands on the
# PATH cmd sees. THAT is why ml64 was "not recognized" for _decimal's vcdiv64.asm
# despite being resolvable in pwsh (pwsh has no such command-line limit).
#
# Rebuild a lean PATH from only the tools the CPython build needs; vcvars then
# adds the MSVC toolset with plenty of headroom. Capture the tool dirs from the
# full PATH *before* trimming it.
$keep = @()
foreach ($tool in 'git.exe', 'python.exe', 'py.exe', 'cmake.exe', 'nuget.exe') {
    $c = Get-Command $tool -ErrorAction SilentlyContinue
    if ($c) { $keep += (Split-Path $c.Source) }
}
$env:PATH = (@(
        "$env:SystemRoot\System32",
        "$env:SystemRoot",
        "$env:SystemRoot\System32\Wbem",
        "$env:SystemRoot\System32\WindowsPowerShell\v1.0"
    ) + $keep | Where-Object { $_ -and (Test-Path $_) } | Select-Object -Unique) -join ';'
Write-Host "DIAG lean PATH length: $($env:PATH.Length)"

# ── Source the MSVC x64 developer environment (vcvars64) into THIS process ──
# Provides cl/link and the MASM assembler (ml64.exe). env-windows.ps1's
# Import-CvcMsvcEnv early-returns when cl is already present (true on CI via
# msvc-dev-cmd), so it would not have sourced vcvars — do it here. Uses the
# robust temp-.cmd technique (avoids the fragile inline `cmd /c "..."` quoting).
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
if (-not (Test-Path $vswhere)) { $vswhere = Join-Path $env:ProgramFiles 'Microsoft Visual Studio\Installer\vswhere.exe' }
$vsRoot = & $vswhere -latest -products '*' `
    -requires 'Microsoft.VisualStudio.Component.VC.Tools.x86.x64' `
    -property installationPath 2>$null | Select-Object -First 1
if (-not $vsRoot) { throw 'vswhere found no VS install with the x64 VC tools' }
$vcvars = Join-Path $vsRoot 'VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $vcvars)) { throw "vcvars64.bat not found under $vsRoot" }
$helper = Join-Path ([System.IO.Path]::GetTempPath()) ("cvc-vcvars-{0}.cmd" -f ([guid]::NewGuid().ToString('N')))
Set-Content -LiteralPath $helper -Value ("@echo off`r`ncall `"$vcvars`" >nul 2>&1`r`nset") -Encoding Ascii
try { $dump = & cmd.exe /c $helper } finally { Remove-Item -LiteralPath $helper -ErrorAction SilentlyContinue }
foreach ($line in $dump) {
    if ($line -match '^([^=]+)=(.*)$') {
        $n = $matches[1]; $v = $matches[2]
        if ($n -in @('_', 'PROMPT')) { continue }
        [Environment]::SetEnvironmentVariable($n, $v, 'Process')
    }
}
Write-Host "DIAG post-vcvars PATH length: $($env:PATH.Length)"
Write-Host "DIAG ml64: $((Get-Command ml64.exe -ErrorAction SilentlyContinue).Source)"
if (-not (Get-Command ml64.exe -ErrorAction SilentlyContinue)) {
    throw 'ml64.exe not resolvable after sourcing vcvars64 — _decimal would fail'
}

# CPython's PCbuild\python.props maps only VisualStudioVersion 15.0-17.0 to a
# platform toolset and otherwise falls back to v140, so under Visual Studio 2026
# (18.0, GitHub's windows-latest image) MSBuild asked for the VS 2015 tools and
# failed with MSB8020.  MSBuild reads environment variables as properties and
# python.props only fills PlatformToolset when it is empty, so name VS 2026's
# toolset here.  Visual Studio 2022 (the fleet, windows-2022) is unaffected.
if ($env:VisualStudioVersion -eq '18.0' -and -not $env:PlatformToolset) {
    $env:PlatformToolset = 'v145'
    Write-Host "Visual Studio 18: PlatformToolset=v145"
}

# 1. Release x64. -e fetches CPython's own externals (OpenSSL, SQLite, bzip2,
#    xz, libffi); --no-tkinter (no tcl/tk here); no --pgo (slow, flaky in CI).
& .\PCbuild\build.bat -e -c Release -p x64 --no-tkinter
if ($LASTEXITCODE -ne 0) { throw "PCbuild\build.bat failed ($LASTEXITCODE)" }

# 2. Stage a relocatable layout into the install prefix.
#
# --include-stable stages python3.dll, the limited-API forwarder. Without it the
# prefix carries only python313.dll, and EVERY abi3 extension module fails to
# import with "DLL load failed while importing _x: The specified module could
# not be found" — because python3.dll is in its import table and is simply not
# there. The error names the extension, never the missing forwarder, and CPython
# then reports a misleading "the Python version for which X was compiled
# (3.12.10, on 3.12) is incompatible with the current interpreter (3.12.10, on 3.12)". Measured
# 2026-08-16 on drjit 1.3.1 (cp312-abi3): python3.dll was the ONLY unresolved
# import of the whole dependency set.
$py = Join-Path (Get-Location) "PCbuild\amd64\python.exe"
# --preset-default (as python311 passes) brings the full stdlib: venv, ensurepip,
# idlelib, lib2to3. Without it the tree has no venv, so `python -m venv` fails.
& $py PC\layout --copy $env:CVC_INSTALL_DIR --preset-default --include-pip --include-dev --include-stable --precompile
if ($LASTEXITCODE -ne 0) { throw "PC\layout failed ($LASTEXITCODE)" }

# 2b. Teach the interpreter where the prefix's DLLs live.
#
# Windows has no RPATH, and since Python 3.8 the loader no longer searches PATH
# for an extension module's dependencies — only the DLL search directories.
# cvcpkg keeps shared libraries in <prefix>\bin, so every extension that links
# one (pillow -> zlib1/jpeg62/tiff, numpy -> openblas, h5py -> hdf5) imports
# with "DLL load failed while importing _x: The specified module could not be
# found" no matter what PATH says. This is the Windows counterpart of the
# $ORIGIN RUNPATH the POSIX builds patch in.
#
# A .pth is the one hook that runs at interpreter startup before any import.
# The path is derived from the running interpreter's own prefix, so the tree
# stays relocatable.
$siteDir = Join-Path $env:CVC_INSTALL_DIR "Lib\site-packages"
New-Item -ItemType Directory -Force -Path $siteDir | Out-Null
$pth = @'
import os, sys; _b = os.path.join(sys.prefix, 'bin'); os.path.isdir(_b) and hasattr(os, 'add_dll_directory') and os.add_dll_directory(_b)
'@
Set-Content -Path (Join-Path $siteDir "cvcpkg-dll-directories.pth") -Value $pth -Encoding ASCII

# 3. Smoke check the staged interpreter.
$staged = Join-Path $env:CVC_INSTALL_DIR "python.exe"
& $staged -c "import ssl, sqlite3, ctypes, lzma, bz2, zlib; import sys; print('cvcpkg: python ' + sys.version.split()[0] + ' ok')"
if ($LASTEXITCODE -ne 0) { throw "staged python smoke check failed ($LASTEXITCODE)" }
