# recipes/wasmedge/build.ps1 — build WasmEdge from source on Windows.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

# In-place fix-up of cmake/Helper.cmake: relax -Werror -> -Wno-error so
# the build doesn't fail on warnings emitted by newer compilers.  Done
# here instead of via a patch file because Strawberry Perl's bundled
# patch.exe 2.5.9 (year 2003) asserts out on any modern unified diff.
$helperCmake = Join-Path (Get-Location) 'cmake\Helper.cmake'
if (Test-Path $helperCmake) {
    (Get-Content $helperCmake -Raw) -replace '(?m)^(\s*)-Werror\s*$', '$1-Wno-error' |
        Set-Content -Encoding ASCII $helperCmake
}

# Locate LLVM installation for AOT support.
# GitHub runners install LLVM via Chocolatey at "C:\Program Files\LLVM".
$llvmArgs = @()
$llvmRoot = "C:\Program Files\LLVM"
$llvmCmake = Join-Path $llvmRoot 'lib\cmake\llvm'
$lldCmake  = Join-Path $llvmRoot 'lib\cmake\lld'

if ((Test-Path $llvmCmake) -and (Test-Path $lldCmake)) {
    Write-Host "Enabling LLVM AOT: $llvmRoot"
    $llvmArgs = @(
        '-DWASMEDGE_USE_LLVM=ON',
        "-DLLVM_DIR=$llvmCmake",
        "-DLLD_DIR=$lldCmake"
    )
} else {
    Write-Host "::warning::LLVM cmake configs not found at $llvmRoot — building without AOT"
    $llvmArgs = @('-DWASMEDGE_USE_LLVM=OFF')
}

# Link-aware build args.  WasmEdge 0.17.0's split shared library on
# Windows is broken upstream: the per-target DLLs reference internal
# symbols (WasmEdge::MMap, WasmEdge::Allocator) that aren't exported,
# producing LNK2019.  For shared builds we therefore still ship a
# DLL but produce it from the bundled static archive (one monolithic
# wasmedge.dll instead of a fan of split DLLs).  For static builds
# we just disable the shared lib.
$linkArgs = if ($env:CVC_LINK -eq 'static') {
    @(
        '-DWASMEDGE_BUILD_SHARED_LIB=OFF',
        '-DWASMEDGE_BUILD_STATIC_LIB=ON',
        '-DWASMEDGE_LINK_LLVM_STATIC=ON'
    )
} else {
    @(
        '-DWASMEDGE_BUILD_SHARED_LIB=ON',
        '-DWASMEDGE_BUILD_STATIC_LIB=OFF'
    )
}

Invoke-CvcCMakeBuild (@(
    '-DWASMEDGE_BUILD_TESTS=OFF',
    '-DWASMEDGE_BUILD_TOOLS=ON',
    '-DWASMEDGE_BUILD_PLUGINS=OFF'
) + $linkArgs + $llvmArgs)
