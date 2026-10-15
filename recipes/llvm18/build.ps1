#!/usr/bin/env pwsh
# recipes/llvm/build.ps1 — build LLVM + Clang + LLD on Windows (MSVC).
#
# Uses MSVC + Ninja via cmake.  The monorepo source is configured from
# the llvm/ subdirectory with LLVM_ENABLE_PROJECTS selecting clang and lld.
$ErrorActionPreference = 'Stop'
$SCRIPT_DIR = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $SCRIPT_DIR "../_common/env-windows.ps1")

$srcLlvm = Join-Path $env:CVC_SOURCE_DIR "llvm"
if (-not (Test-Path $srcLlvm)) {
    throw "LLVM monorepo source not found at $srcLlvm"
}

# LLVM_ENABLE_DIA_SDK=OFF: LLVM turns the DIA SDK on whenever the Visual Studio
# running the build has one ($env:VSINSTALLDIR\DIA SDK), and the exported
# LLVMDebugInfoPDB target then links diaguids.lib by that absolute path
# (lib/cmake/llvm/LLVMExports.cmake, INTERFACE_LINK_LIBRARIES
# "C:/Program Files/Microsoft Visual Studio/2022/Enterprise/DIA SDK/lib/amd64/diaguids.lib").
# Every consumer that links DebugInfoPDB, directly or through lld's COFF driver,
# then needs Visual Studio installed at exactly the builder's path.  With it OFF
# only the DIA-backed PDB reader is left out: DebugInfoPDB keeps its native PDB
# reader and writer, which lld-link and llvm-symbolizer use by default.
cmake -G Ninja `
    -S "$srcLlvm" `
    -B "$env:CVC_BUILD_DIR" `
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR" `
    -DCMAKE_BUILD_TYPE=Release `
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON `
    -DCMAKE_CXX_STANDARD=17 `
    "-DCMAKE_PREFIX_PATH=$env:CVC_DEPS_PREFIX" `
    -DLLVM_ENABLE_PROJECTS="clang;lld" `
    -DLLVM_TARGETS_TO_BUILD="X86;AArch64;WebAssembly" `
    -DLLVM_INCLUDE_TESTS=OFF `
    -DLLVM_INCLUDE_BENCHMARKS=OFF `
    -DLLVM_INCLUDE_EXAMPLES=OFF `
    -DLLVM_ENABLE_ZLIB=FORCE_ON `
    -DLLVM_ENABLE_LIBXML2=OFF `
    -DLLVM_ENABLE_TERMINFO=OFF `
    -DLLVM_ENABLE_BINDINGS=OFF `
    -DLLVM_ENABLE_DIA_SDK=OFF `
    -DLLVM_PARALLEL_LINK_JOBS=2 `
    -DCLANG_INCLUDE_DOCS=OFF `
    -DCLANG_INCLUDE_TESTS=OFF `
    -DCLANG_BUILD_TOOLS=ON

if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw "cmake install failed" }

# ── The exported CMake package must not name the builder's Visual Studio ─────
# An absolute Visual Studio or Windows SDK path in an installed .cmake file (an
# imported target's INTERFACE_LINK_LIBRARIES, say) resolves only on a machine
# with the builder's edition, version and install location, so a consumer on
# another runner image or a Build Tools install fails to link.  The pattern
# needs a drive letter, so comments that only mention the products, and URLs
# ("https://..."), do not match.
$cmakeRoot = Join-Path $env:CVC_INSTALL_DIR 'lib\cmake'
$cmakeFiles = @(Get-ChildItem -LiteralPath $cmakeRoot -Recurse -File -Filter '*.cmake' -ErrorAction SilentlyContinue)
if ($cmakeFiles.Count -eq 0) { throw "llvm18: no .cmake files installed under $cmakeRoot" }
$hostSdkPath = '(?i)(?<![A-Za-z0-9])[A-Za-z]:[\\/][^"''\r\n;]*(Microsoft Visual Studio|Windows Kits)'
$hits = @($cmakeFiles | Select-String -Pattern $hostSdkPath)
if ($hits.Count -gt 0) {
    foreach ($h in $hits) {
        $rel = $h.Path.Substring($env:CVC_INSTALL_DIR.TrimEnd('\', '/').Length).TrimStart('\', '/')
        Write-Host "llvm18: ${rel}:$($h.LineNumber): $($h.Line.Trim())"
    }
    throw "llvm18: $($hits.Count) line(s) in the installed lib/cmake files name an absolute Visual Studio or Windows Kits path"
}
$dia = Select-String -LiteralPath (Join-Path $cmakeRoot 'llvm\LLVMConfig.cmake') -Pattern '^set\(LLVM_ENABLE_DIA_SDK ' |
    Select-Object -First 1
$diaNote = if ($dia) { $dia.Line.Trim() } else { 'LLVMConfig.cmake records no LLVM_ENABLE_DIA_SDK' }
Write-Host "llvm18: checked $($cmakeFiles.Count) .cmake files under lib/cmake: no absolute Microsoft Visual Studio or Windows Kits path ($diaNote)"
