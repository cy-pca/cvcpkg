#!/usr/bin/env pwsh
# recipes/llvm20/build.ps1 — build LLVM 20 core on Windows (MSVC).
#
# Uses MSVC + Ninja via cmake.  The monorepo source is configured from the
# llvm/ subdirectory.  Unlike recipes/llvm, LLVM_ENABLE_PROJECTS is left empty:
# the only consumer (llvm-cbe) references neither clang nor lld.
$ErrorActionPreference = 'Stop'
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SCRIPT_DIR "../_common/env-windows.ps1")

$srcLlvm = Join-Path $env:CVC_SOURCE_DIR "llvm"
if (-not (Test-Path $srcLlvm)) {
    throw "LLVM monorepo source not found at $srcLlvm"
}

$LinkJobs = [Math]::Max(1, [Math]::Min(2, [int]$env:CVC_JOBS))

cmake -G Ninja `
    -S $srcLlvm `
    -B $env:CVC_BUILD_DIR `
    -DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR `
    -DCMAKE_BUILD_TYPE=Release `
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON `
    -DCMAKE_CXX_STANDARD=17 `
    -DCMAKE_PREFIX_PATH=$env:CVC_DEPS_PREFIX `
    -DLLVM_TARGETS_TO_BUILD="X86;AArch64;WebAssembly" `
    -DLLVM_BUILD_LLVM_DYLIB=ON `
    -DLLVM_LINK_LLVM_DYLIB=ON `
    -DLLVM_INCLUDE_TESTS=OFF `
    -DLLVM_INCLUDE_BENCHMARKS=OFF `
    -DLLVM_INCLUDE_EXAMPLES=OFF `
    -DLLVM_ENABLE_ZLIB=FORCE_ON `
    -DLLVM_ENABLE_LIBXML2=OFF `
    -DLLVM_ENABLE_TERMINFO=OFF `
    -DLLVM_ENABLE_BINDINGS=OFF `
    -DLLVM_PARALLEL_LINK_JOBS=$LinkJobs

if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }
