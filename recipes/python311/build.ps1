#!/usr/bin/env pwsh
# recipes/python311/build.ps1 — build CPython 3.11 on Windows from source.
#
# CPython's Windows build is self-contained: PCbuild\build.bat -e runs
# get_externals.bat, which fetches CPython's OWN bundled externals (openssl,
# libffi, bzip2, xz, sqlite, zlib, ...). So the windows build needs NONE of the
# cvcpkg deps (they are scoped off windows in recipe.yaml). PC\layout then
# produces a complete python.org-style tree (python.exe + python3XX.dll + Lib/ +
# DLLs/ + include/ + libs/python3XX.lib) that find_package(Python3
# Development.Embed) expects.
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

# Release x64. -e fetches externals; --no-tkinter (no tcl/tk here). No --pgo: the
# profile-guided build reruns the test suite and is slow + flaky in CI.
& .\PCbuild\build.bat -e -c Release -p x64 --no-tkinter
if ($LASTEXITCODE -ne 0) { throw "PCbuild\build.bat failed (exit $LASTEXITCODE)" }

# Lay out a full install directly into the install dir. --include-dev carries the
# headers + libs/python311.lib needed to EMBED (volrover3), --include-pip carries
# pip, --precompile bytes-compiles the stdlib.
& .\PCbuild\amd64\python.exe PC\layout `
    --copy $env:CVC_INSTALL_DIR `
    --preset-default `
    --include-dev `
    --include-pip `
    --precompile
if ($LASTEXITCODE -ne 0) { throw "PC\layout failed (exit $LASTEXITCODE)" }
