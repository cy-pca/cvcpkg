# recipes/libpq/build.ps1 — build libpq (PostgreSQL client) on Windows.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Set-Location $env:CVC_SOURCE_DIR

# Meson-based build for libpq only.
& meson setup $env:CVC_BUILD_DIR `
    "--prefix=$env:CVC_INSTALL_DIR" `
    '--buildtype=release' `
    '-Dlibpq=true' `
    '-Dssl=openssl' `
    '-Dzlib=enabled' `
    '-Dreadline=disabled' `
    '-Dzstd=disabled' `
    '-Dlz4=disabled' `
    '-Dnls=disabled'
if ($LASTEXITCODE -ne 0) { throw "meson setup failed" }

Set-Location $env:CVC_BUILD_DIR
& ninja -j $env:CVC_JOBS 'src/interfaces/libpq:pq'
if ($LASTEXITCODE -ne 0) { throw "ninja build failed" }

& ninja install
if ($LASTEXITCODE -ne 0) { throw "ninja install failed" }

# Generate CMake config.
$cmakeDir = "$env:CVC_INSTALL_DIR\lib\cmake\PostgreSQL"
New-Item -ItemType Directory -Force -Path $cmakeDir | Out-Null

@"
get_filename_component(_PG_PREFIX "`${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
add_library(PostgreSQL::PostgreSQL UNKNOWN IMPORTED)
find_library(_PG_LIB NAMES pq libpq PATHS "`${_PG_PREFIX}/lib" NO_DEFAULT_PATH)
set_target_properties(PostgreSQL::PostgreSQL PROPERTIES
    IMPORTED_LOCATION "`${_PG_LIB}"
    INTERFACE_INCLUDE_DIRECTORIES "`${_PG_PREFIX}/include"
)
set(PostgreSQL_FOUND TRUE)
set(PostgreSQL_INCLUDE_DIRS "`${_PG_PREFIX}/include")
set(PostgreSQL_LIBRARIES "`${_PG_LIB}")
unset(_PG_PREFIX)
unset(_PG_LIB)
"@ | Set-Content "$cmakeDir\PostgreSQLConfig.cmake"
