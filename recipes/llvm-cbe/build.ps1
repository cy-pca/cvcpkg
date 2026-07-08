#!/usr/bin/env pwsh
# recipes/llvm-cbe/build.ps1 — build the LLVM C Backend on Windows (MSVC).
$ErrorActionPreference = 'Stop'
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SCRIPT_DIR "../_common/env-windows.ps1")

cmake -G Ninja `
    -S $env:CVC_SOURCE_DIR `
    -B $env:CVC_BUILD_DIR `
    -DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR `
    -DCMAKE_BUILD_TYPE=Release `
    -DCMAKE_CXX_STANDARD=17 `
    -DCMAKE_PREFIX_PATH=$env:CVC_DEPS_PREFIX `
    -DLLVM_DIR="$($env:CVC_DEPS_PREFIX)/lib/cmake/llvm"

if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

cmake --build $env:CVC_BUILD_DIR --target llvm-cbe -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }
