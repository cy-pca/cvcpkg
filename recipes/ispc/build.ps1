# recipes/ispc/build.ps1 — build the ISPC compiler on Windows (MSVC) against cvcpkg's LLVM/Clang.
# Same switches as build.sh (see the comment there).  Windows-only differences:
#   - MSVC builds no libLLVM DLL, so llvm-config hands ISPC LLVM's static
#     component libraries.  ISPC links only a fixed list of Windows system
#     libraries on top (it never asks llvm-config --system-libs there), which
#     misses zlib: cvcpkg's LLVMSupport is built with LLVM_ENABLE_ZLIB.  Pass
#     llvm-config's own --system-libs to the link, with zlib's STATIC library
#     in place of its import library: ISPC links with /DEPENDENTLOADFLAG:0x800,
#     so ispc.exe resolves its DLL imports from System32 only and can never
#     load zlib1.dll (linked against the import library, ispc-slim.exe died
#     silently on its first run in the build).
#   - Python is the prefix's python.exe (python313), never a python on PATH:
#     on a stock Windows box that name is the Microsoft Store alias stub.
#   - m4 is cvcpkg's m4.exe; bison.exe and flex.exe (winflexbison) are found
#     on PATH, the build prefix's bin first.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$roots = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ } | Select-Object -Unique
function Find-CvcPrefixFile([string]$rel) {
    foreach ($root in $roots) {
        $cand = Join-Path $root $rel
        if (Test-Path -LiteralPath $cand) { return $cand }
    }
    throw "ispc: no $rel in CVC_BUILD_PREFIX ($env:CVC_BUILD_PREFIX) or CVC_DEPS_PREFIX ($env:CVC_DEPS_PREFIX)"
}

$llvmConfig = Find-CvcPrefixFile 'bin\llvm-config.exe'
$python = Find-CvcPrefixFile 'python.exe'
$m4 = Find-CvcPrefixFile 'bin\m4.exe'
$llvmVer = & $llvmConfig --version
if ($LASTEXITCODE -ne 0) { throw "ispc: $llvmConfig does not run" }
Write-Host "ispc: LLVM $llvmVer from $llvmConfig"

$sysLibs = @((& $llvmConfig --link-static --system-libs) -split '\s+' | Where-Object { $_ })
if ($LASTEXITCODE -ne 0) { throw "ispc: $llvmConfig --system-libs failed" }
# llvm-config names zlib z.lib (zlib.pc's -lz); cvcpkg's zlib ships that as a
# copy of the DLL import library, so swap in zlibstatic.lib (built /MD, like
# ISPC).
$zlibStatic = Find-CvcPrefixFile 'lib\zlibstatic.lib'
$sysLibs = @($sysLibs | ForEach-Object {
    if ($_ -match '^(lib)?z(lib)?d?\.lib$') { '"' + $zlibStatic + '"' } else { $_ }
})
$linkFlags = $sysLibs -join ' '
Write-Host "ispc: extra link flags: $linkFlags"

# CMake wants forward slashes in paths it pastes into generated commands.
$llvmConfigFwd = $llvmConfig -replace '\\', '/'
$pythonFwd = $python -replace '\\', '/'
$m4Fwd = $m4 -replace '\\', '/'

Invoke-CvcCMakeBuild @(
    "-DLLVM_CONFIG_EXECUTABLE=$llvmConfigFwd",
    "-DPython3_EXECUTABLE=$pythonFwd",
    '-DPython3_FIND_REGISTRY=NEVER',
    "-DM4_EXECUTABLE=$m4Fwd",
    "-DCMAKE_EXE_LINKER_FLAGS=$linkFlags",
    '-DISPC_64BIT_ONLY=ON',
    '-DISPC_INCLUDE_EXAMPLES=OFF',
    '-DISPC_INCLUDE_DPCPP_EXAMPLES=OFF',
    '-DISPC_INCLUDE_TESTS=OFF',
    '-DISPC_INCLUDE_BENCHMARKS=OFF',
    '-DISPC_INCLUDE_RT=OFF',
    '-DISPC_INCLUDE_UTILS=OFF',
    '-DISPC_LIBRARY=OFF',
    '-DISPC_LIBRARY_JIT=OFF',
    '-DXE_ENABLED=OFF',
    '-DWASM_ENABLED=OFF',
    '-DRISCV_ENABLED=OFF',
    '-DGIT_BINARY=GIT_BINARY-NOTFOUND',
    '-DX86_ENABLED=ON',
    '-DARM_ENABLED=OFF'
)

# Smoke test: the installed compiler starts and compiles a kernel for the
# host's default target (stdlib and builtins come from inside the binary).
# A DLL it cannot load outside System32 shows up here as a silent exit
# 0xC0000135, so print the exit code.
$ispc = Join-Path $env:CVC_INSTALL_DIR 'bin\ispc.exe'
& $ispc --version
if ($LASTEXITCODE -ne 0) { throw ("ispc: $ispc --version failed (exit 0x{0:X8})" -f $LASTEXITCODE) }
$smoke = Join-Path $env:CVC_BUILD_DIR 'cvc-smoke'
New-Item -ItemType Directory -Force -Path $smoke | Out-Null
$src = Join-Path $smoke 'scale.ispc'
Set-Content -LiteralPath $src -Encoding ascii -Value @'
export void scale(uniform float data[], uniform int n, uniform float k) {
    foreach (i = 0 ... n) {
        data[i] = data[i] * k + sqrt(abs(data[i]));
    }
}
'@
$obj = Join-Path $smoke 'scale.obj'
& $ispc -O2 $src -o $obj -h (Join-Path $smoke 'scale_ispc.h')
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $obj)) { throw "ispc: smoke test failed to compile $src" }
Write-Host "ispc: smoke test compiled $obj"
