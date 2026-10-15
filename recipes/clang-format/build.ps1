#!/usr/bin/env pwsh
# recipes/clang-format/build.ps1 — Windows counterpart of build.sh: build only
# clang-format from the LLVM monorepo, with MSVC + Ninja.
#
# Everything is static, as on Unix: LLVM's and clang's component libraries, and
# the C/C++ runtime (/MT, CMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded, the
# counterpart of -static-libstdc++), so bin\clang-format.exe needs no MSVC
# redistributable and the bundle relocates trivially.
$ErrorActionPreference = 'Stop'
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SCRIPT_DIR "../_common/env-windows.ps1")

$srcLlvm = Join-Path $env:CVC_SOURCE_DIR 'llvm'
if (-not (Test-Path $srcLlvm) -or -not (Test-Path (Join-Path $env:CVC_SOURCE_DIR 'clang'))) {
    throw "cvcpkg: $env:CVC_SOURCE_DIR is not the LLVM monorepo (no llvm\ or clang\)"
}

# clang-format generates no code, so no backend is needed; LLVM still wants one
# target to configure, and the host's is the cheapest. LLVM_ENABLE_DIA_SDK=OFF
# for the reason llvm18/build.ps1 gives (no absolute Visual Studio path may leak
# into what is built), though clang-format does not link DebugInfoPDB.
cmake -G Ninja `
    -S "$srcLlvm" `
    -B "$env:CVC_BUILD_DIR" `
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR" `
    -DCMAKE_BUILD_TYPE=Release `
    -DCMAKE_MSVC_RUNTIME_LIBRARY=MultiThreaded `
    -DLLVM_ENABLE_PROJECTS=clang `
    -DLLVM_TARGETS_TO_BUILD=host `
    -DBUILD_SHARED_LIBS=OFF `
    -DLLVM_BUILD_LLVM_DYLIB=OFF `
    -DLLVM_LINK_LLVM_DYLIB=OFF `
    -DLLVM_ENABLE_ZLIB=OFF `
    -DLLVM_ENABLE_ZSTD=OFF `
    -DLLVM_ENABLE_TERMINFO=OFF `
    -DLLVM_ENABLE_LIBXML2=OFF `
    -DLLVM_ENABLE_LIBEDIT=OFF `
    -DLLVM_ENABLE_BINDINGS=OFF `
    -DLLVM_ENABLE_DIA_SDK=OFF `
    -DLLVM_INCLUDE_TESTS=OFF `
    -DLLVM_INCLUDE_BENCHMARKS=OFF `
    -DLLVM_INCLUDE_EXAMPLES=OFF `
    -DLLVM_INCLUDE_DOCS=OFF `
    -DCLANG_INCLUDE_TESTS=OFF `
    -DCLANG_INCLUDE_DOCS=OFF `
    -DCLANG_ENABLE_ARCMT=OFF `
    -DCLANG_ENABLE_STATIC_ANALYZER=OFF
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

cmake --build "$env:CVC_BUILD_DIR" --target clang-format -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

$bin = Join-Path $env:CVC_INSTALL_DIR 'bin'
$share = Join-Path $env:CVC_INSTALL_DIR 'share\clang'
New-Item -ItemType Directory -Force -Path $bin, $share | Out-Null
$exe = Join-Path $bin 'clang-format.exe'
Copy-Item -LiteralPath (Join-Path $env:CVC_BUILD_DIR 'bin\clang-format.exe') -Destination $exe
$toolDir = Join-Path $env:CVC_SOURCE_DIR 'clang\tools\clang-format'
Copy-Item -LiteralPath (Join-Path $toolDir 'git-clang-format') -Destination (Join-Path $bin 'git-clang-format')
Copy-Item -LiteralPath (Join-Path $toolDir 'clang-format-diff.py') -Destination (Join-Path $share 'clang-format-diff.py')

# Same smoke test as build.sh: the version is the pinned one, and the binary
# formats a snippet (its output may end in CRLF here).
$version = (& $exe --version) -join "`n"
if ($LASTEXITCODE -ne 0) { throw "clang-format --version failed ($LASTEXITCODE)" }
Write-Host $version
if ($version -notmatch 'clang-format version 18\.') { throw "cvcpkg: built clang-format is not 18.x: $version" }
$out = ('int  main( ){return 0;}' | & $exe --style=LLVM) -join "`n"
if ($LASTEXITCODE -ne 0) { throw "clang-format smoke test failed ($LASTEXITCODE)" }
if ($out.TrimEnd("`r", "`n") -ne 'int main() { return 0; }') {
    throw "cvcpkg: clang-format smoke test produced: $out"
}
Write-Host "clang-format smoke test OK"
