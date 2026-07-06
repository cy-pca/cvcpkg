# recipes/sqlite/build.ps1 — build SQLite from source on Windows (MSVC).
$ErrorActionPreference = 'Stop'

if (-not $env:CVC_SOURCE_DIR)  { throw 'CVC_SOURCE_DIR must be set' }
if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }
if (-not $env:CVC_JOBS)        { $env:CVC_JOBS = [Environment]::ProcessorCount }

# The sqlite-autoconf tarball includes a Makefile.msc for NMAKE.
# We invoke it directly since autotools is unavailable on MSVC.
Push-Location $env:CVC_SOURCE_DIR
try {
    $prefix    = $env:CVC_INSTALL_DIR
    $libDir    = "$prefix\lib"
    $incDir    = "$prefix\include"
    $binDir    = "$prefix\bin"

    New-Item -ItemType Directory -Force -Path $libDir, $incDir, $binDir | Out-Null

    # Compile the amalgamation with NMAKE using the provided Makefile.msc.
    & nmake /f Makefile.msc `
        "PREFIX=$prefix" `
        "USE_NATIVE_LIBPATHS=1" `
        sqlite3.dll sqlite3.lib shell.exe

    Copy-Item sqlite3.dll  $binDir -Force
    Copy-Item sqlite3.lib  $libDir -Force
    Copy-Item sqlite3.h    $incDir -Force
    Copy-Item sqlite3ext.h $incDir -Force
    Copy-Item shell.exe "$binDir\sqlite3.exe" -Force

    # Generate a pkg-config file.
    New-Item -ItemType Directory -Force -Path "$libDir\pkgconfig" | Out-Null
    @"
prefix=$($prefix -replace '\\','/')
exec_prefix=`${prefix}
libdir=`${exec_prefix}/lib
includedir=`${prefix}/include

Name: SQLite3
Description: SQL database engine
Version: 3
Libs: -L`${libdir} -lsqlite3
Cflags: -I`${includedir}
"@ | Set-Content "$libDir\pkgconfig\sqlite3.pc"
} finally {
    Pop-Location
}
