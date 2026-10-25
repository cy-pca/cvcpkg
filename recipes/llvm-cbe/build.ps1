#!/usr/bin/env pwsh
# recipes/llvm-cbe/build.ps1 — build the LLVM C Backend on Windows (MSVC).
$ErrorActionPreference = 'Stop'
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SCRIPT_DIR "../_common/env-windows.ps1")

# Quote every -DNAME=$value: PowerShell passes an unquoted argument that
# starts with '-' to a native command verbatim, so CMake got the literal
# text '$env:CVC_INSTALL_DIR' / '$LinkJobs' (windows populate 2026-10-19:
# "Invalid pool defined by property 'JOB_POOLS': link_job_pool=$LinkJobs").
cmake -G Ninja `
    -S $env:CVC_SOURCE_DIR `
    -B $env:CVC_BUILD_DIR `
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR" `
    -DCMAKE_BUILD_TYPE=Release `
    -DCMAKE_CXX_STANDARD=17 `
    "-DCMAKE_PREFIX_PATH=$env:CVC_DEPS_PREFIX" `
    -DLLVM_DIR="$($env:CVC_DEPS_PREFIX)/lib/cmake/llvm"

if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

cmake --build $env:CVC_BUILD_DIR --target llvm-cbe -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }

# As build.sh: built against an installed LLVM, the tool target has no install
# rule (add_llvm_tool installs only inside an LLVM build tree), so the install
# step stages just the two libraries (job 23013).  Install the exe the build made.
$dest = Join-Path $env:CVC_INSTALL_DIR 'bin\llvm-cbe.exe'
if (-not (Test-Path $dest)) {
    $exe = Get-ChildItem -Path $env:CVC_BUILD_DIR -Recurse -File -Filter 'llvm-cbe.exe' | Select-Object -First 1
    if (-not $exe) { throw "llvm-cbe: no llvm-cbe.exe under $env:CVC_BUILD_DIR" }
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    Copy-Item -LiteralPath $exe.FullName -Destination $dest
}
