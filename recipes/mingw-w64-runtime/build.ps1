# recipes/mingw-w64-runtime/build.ps1 — stage ONLY the redistributable runtime
# DLLs out of the WinLibs archive.
#
# Sibling of mingw-w64-gcc, same archive and sha256, opposite purpose:
#   mingw-w64-gcc      host tool. Compilers and binutils. MUST NOT be installed
#                      into the runtime prefix — its bin/ shadows MSVC for
#                      every later CMake build against that prefix.
#   mingw-w64-runtime  runtime dependency. Just the DLLs a MinGW-built artifact
#                      imports by name. Belongs in the runtime prefix.
$ErrorActionPreference = 'Stop'

if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }
if (-not $env:CVC_SOURCE_DIR)  { throw 'CVC_SOURCE_DIR must be set' }

$srcBin = Join-Path $env:CVC_SOURCE_DIR 'bin'
$dstBin = Join-Path $env:CVC_INSTALL_DIR 'bin'
New-Item -ItemType Directory -Force -Path $dstBin | Out-Null

# Explicit list, not a glob: the archive's bin/ holds ~70 DLLs, most of them
# support libraries for gdb/binutils that no compiled artifact imports. Only
# the GCC/threading redistributables belong in a runtime prefix.
$runtime = @(
    'libwinpthread-1.dll'   # POSIX threads shim; x264 and anything -pthread
    'libgcc_s_seh-1.dll'    # GCC unwinder / support routines
    'libstdc++-6.dll'       # C++ standard library
    'libgomp-1.dll'         # OpenMP
    'libquadmath-0.dll'     # __float128, pulled in by gfortran code
    'libatomic-1.dll'       # out-of-line atomics
    'libssp-0.dll'          # stack-protector helpers
    'libmcfgthread-2.dll'   # the MCF thread model's own runtime
)

$staged = @()
foreach ($name in $runtime) {
    $p = Join-Path $srcBin $name
    if (Test-Path $p) { Copy-Item $p $dstBin -Force; $staged += $name }
}

# libwinpthread is the one every consumer hits first, and a silently empty
# package would reintroduce exactly the "cannot start from a clean prefix"
# failure this recipe exists to remove.
if ($staged -notcontains 'libwinpthread-1.dll') {
    throw ("mingw-w64-runtime: libwinpthread-1.dll not found in the archive's bin/. " +
           "The upstream layout changed; update the list in build.ps1.")
}

Write-Host "mingw-w64-runtime: staged $($staged.Count) runtime DLL(s):"
$staged | ForEach-Object { Write-Host "  $_" }
