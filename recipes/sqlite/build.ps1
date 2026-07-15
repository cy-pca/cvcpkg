# recipes/sqlite/build.ps1 — build SQLite from source on Windows (MSVC).
$ErrorActionPreference = 'Stop'

# Import the MSVC environment (puts cl/nmake/link on PATH).  Native
# builder sessions may already have it, but winhost delegation and any
# clean shell do not — recipes must self-import, like zlib does.
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

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
        sqlite3.dll sqlite3.lib
    if ($LASTEXITCODE -ne 0) { throw "nmake (sqlite3.dll/sqlite3.lib) failed" }

    # Build the CLI standalone from the amalgamation (shell.c + sqlite3.c).
    # `nmake shell.exe` links the CLI against the DLL import lib, but shell.c
    # uses sqlite3 functions the DLL does not export (the internal
    # sqlite3_win32_* helpers, sqlite3_deserialize, ...), producing 133
    # LNK2019 "unresolved external symbol" errors.  Compiling the amalgamation
    # directly links the CLI statically and resolves every symbol.
    & cl /nologo /O2 /I. shell.c sqlite3.c /Fe:sqlite3.exe
    if ($LASTEXITCODE -ne 0) { throw "sqlite CLI (sqlite3.exe) build failed" }

    Copy-Item sqlite3.dll  $binDir -Force
    Copy-Item sqlite3.lib  $libDir -Force
    Copy-Item sqlite3.h    $incDir -Force
    Copy-Item sqlite3ext.h $incDir -Force
    Copy-Item sqlite3.exe  "$binDir\sqlite3.exe" -Force

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
