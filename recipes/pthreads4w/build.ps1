# recipes/pthreads4w/build.ps1 — build pthreads4w with MSVC nmake.
#
# pthreads4w has no CMake, only a hand-rolled Makefile with named
# targets:  "VC" (DLL, /MD), "VC-static" (static lib, /MT).  Run
# whichever matches the current CVC_LINK setting from vcvars-loaded
# nmake, then stage the outputs.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# nmake wants source-tree cwd; copy the vendored sources into the build
# dir so we don't pollute the (read-only-ish) source tree with build
# artefacts.
Copy-Item -Recurse -Force "$env:CVC_SOURCE_DIR\*" $env:CVC_BUILD_DIR
Push-Location $env:CVC_BUILD_DIR
try {
    $target = if ($env:CVC_LINK -eq 'static') { 'VC-static' } else { 'VC' }
    Write-Host "cvcpkg: pthreads4w -> nmake $target"
    & nmake /nologo /f Makefile clean 2>$null
    & nmake /nologo /f Makefile $target
    if ($LASTEXITCODE -ne 0) { throw "nmake $target failed" }

    # Manual staging — the shipped `install` target only works with
    # its own DESTROOT var and expects the source layout.
    $installLib     = Join-Path $env:CVC_INSTALL_DIR 'lib'
    $installBin     = Join-Path $env:CVC_INSTALL_DIR 'bin'
    $installInclude = Join-Path $env:CVC_INSTALL_DIR 'include'
    foreach ($d in @($installLib, $installBin, $installInclude)) {
        New-Item -ItemType Directory -Force -Path $d | Out-Null
    }

    # Header files
    foreach ($h in @('pthread.h','sched.h','semaphore.h','_ptw32.h')) {
        if (Test-Path $h) {
            Copy-Item -Force $h $installInclude
        }
    }

    # Libraries
    Get-ChildItem -File -Path . -Include 'pthreadV*.lib','libpthreadV*.lib' `
        | ForEach-Object { Copy-Item -Force $_.FullName $installLib }
    if ($env:CVC_LINK -ne 'static') {
        Get-ChildItem -File -Path . -Include 'pthreadV*.dll' `
            | ForEach-Object { Copy-Item -Force $_.FullName $installBin }
    }

    if (-not (Get-ChildItem $installLib -Filter 'pthreadV*.lib' -ErrorAction SilentlyContinue) `
        -and -not (Get-ChildItem $installLib -Filter 'libpthreadV*.lib' -ErrorAction SilentlyContinue)) {
        throw "no pthreadV*.lib produced by nmake $target"
    }

    Write-Host "cvcpkg: pthreads4w staged to $env:CVC_INSTALL_DIR"
}
finally {
    Pop-Location
}
