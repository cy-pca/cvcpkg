# recipes/wasmedge/build.ps1 — build WasmEdge from source on Windows.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

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

Invoke-CvcCMakeBuild (@(
    '-DWASMEDGE_BUILD_TESTS=OFF',
    '-DWASMEDGE_BUILD_TOOLS=ON',
    '-DWASMEDGE_BUILD_PLUGINS=OFF',
    '-DWASMEDGE_BUILD_SHARED_LIB=ON',
    '-DWASMEDGE_BUILD_STATIC_LIB=ON'
) + $llvmArgs)
