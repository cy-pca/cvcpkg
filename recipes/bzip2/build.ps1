# recipes/bzip2/build.ps1 — build bzip2 from source on Windows using MSVC.
#
# Upstream ships `makefile.msc` (nmake-based) which builds the static
# library and CLI tools.  We also emit a DLL by compiling the six
# library sources with cl.exe directly.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

$src = $env:CVC_SOURCE_DIR
$install = $env:CVC_INSTALL_DIR

New-Item -ItemType Directory -Force -Path "$install\bin" | Out-Null
New-Item -ItemType Directory -Force -Path "$install\lib" | Out-Null
New-Item -ItemType Directory -Force -Path "$install\include" | Out-Null
New-Item -ItemType Directory -Force -Path "$install\lib\pkgconfig" | Out-Null

Push-Location $src
try {
    # Static library and CLI tools via upstream makefile.msc.
    & nmake /nologo /f makefile.msc
    if ($LASTEXITCODE -ne 0) { throw "bzip2 nmake failed" }

    # DLL: compile the six library sources with LIBBZ2_DLL to export the API.
    $libSrc = @('blocksort.c','huffman.c','crctable.c','randtable.c','compress.c','decompress.c','bzlib.c')
    & cl /nologo /O2 /W3 /MD /DBZ_LIB /DLIBBZ2_DLL $libSrc /link /DLL /IMPLIB:libbz2.lib /OUT:libbz2.dll
    if ($LASTEXITCODE -ne 0) { throw "libbz2.dll build failed" }

    Copy-Item 'libbz2.dll'  "$install\bin\"
    Copy-Item 'libbz2.lib'  "$install\lib\"
    if (Test-Path 'libbz2.exp') { Copy-Item 'libbz2.exp' "$install\lib\" }
    Copy-Item 'libbz2.lib'  "$install\lib\bz2.lib" -Force
    if (Test-Path 'bzip2.exe') { Copy-Item 'bzip2.exe' "$install\bin\" }
    if (Test-Path 'bzip2recover.exe') { Copy-Item 'bzip2recover.exe' "$install\bin\" }
    Copy-Item 'bzlib.h' "$install\include\"
} finally {
    Pop-Location
}

$pc = @'
prefix=${pcfiledir}/../..
exec_prefix=${prefix}
libdir=${exec_prefix}/lib
includedir=${prefix}/include

Name: bzip2
Description: Burrows-Wheeler compression library
URL: https://sourceware.org/bzip2/
Version: 1.0.8
Libs: -L${libdir} -lbz2
Cflags: -I${includedir}
'@
Set-Content -NoNewline -Path "$install\lib\pkgconfig\bzip2.pc" -Value $pc

if (Get-Command Invoke-CvcRewriteInstallPaths -ErrorAction SilentlyContinue) {
    Invoke-CvcRewriteInstallPaths
}
