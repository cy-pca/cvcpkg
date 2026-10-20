# recipes/libpq/build.ps1 — build libpq (PostgreSQL client) on Windows.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

# PostgreSQL 18+ uses Meson.  Locate a Python interpreter (via the
# Windows 'py' launcher, then 'python3'/'python' on PATH, then a
# filesystem scan of well-known install roots).  The cvcpkg builder
# daemon on phm-win11 runs as SYSTEM and inherits a PATH that does
# not include the per-user Python install directory, and 'py -3'
# reports "No installed Python found!" because PEP 397's registry
# lookup only sees HKCU entries — SYSTEM has its own HKCU with no
# Python.  Scan the filesystem as a fallback so this recipe works
# with a per-user Python install too.
function Get-CvcPythonInvocation {
    # Returns a hashtable @{Exe='...python.exe'; Args=@('-3')} (or Args=@()).
    # Using a hashtable rather than an array avoids PowerShell's
    # single-element array unwrapping (a bare `@($exe)` return degrades
    # into the string, then `$pyInvoke[0]` indexes character 'C').
    foreach ($candidate in @('py', 'python3', 'python')) {
        $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
        if (-not $cmd) { continue }
        $verifyArgs = if ($candidate -eq 'py') { @('-3', '--version') } else { @('--version') }
        try {
            $null = & $cmd.Source @verifyArgs 2>&1
            if ($LASTEXITCODE -eq 0) {
                if ($candidate -eq 'py') {
                    return @{ Exe = $cmd.Source; Args = @('-3') }
                }
                return @{ Exe = $cmd.Source; Args = @() }
            }
        } catch { }
    }
    # Fallback: scan filesystem for python.exe. Newest version wins.
    $roots = @(
        'C:\Program Files\Python313', 'C:\Program Files\Python312',
        'C:\Program Files\Python311', 'C:\Program Files\Python310',
        'C:\Python313', 'C:\Python312', 'C:\Python311', 'C:\Python310'
    )
    Get-ChildItem 'C:\Users\*\AppData\Local\Programs\Python\Python3*' -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | ForEach-Object { $roots += $_.FullName }
    foreach ($root in $roots) {
        $exe = Join-Path $root 'python.exe'
        if (Test-Path $exe) {
            Write-Host "cvcpkg: using Python at $exe (filesystem fallback)"
            return @{ Exe = $exe; Args = @() }
        }
    }
    throw "No Python interpreter found (tried: py, python3, python; scanned Program Files, C:\Python*, and per-user AppData installs)"
}

$mesonCmd = Get-Command meson -ErrorAction SilentlyContinue
if (-not $mesonCmd) {
    Write-Host "cvcpkg: meson not found on PATH; installing via pip ..."
    $py = Get-CvcPythonInvocation
    & $py.Exe @($py.Args + @(
        '-m', 'pip', 'install', '--disable-pip-version-check', '--quiet', 'meson'
    ))
    if ($LASTEXITCODE -ne 0) { throw "pip install meson failed" }
    $mesonCmd = Get-Command meson -ErrorAction SilentlyContinue
}

if ($mesonCmd) {
    $mesonExe  = $mesonCmd.Source
    $mesonArgs = @()
} else {
    $py        = Get-CvcPythonInvocation
    $mesonExe  = $py.Exe
    $mesonArgs = @($py.Args + @('-m', 'mesonbuild.mesonmain'))
}

Set-Location $env:CVC_SOURCE_DIR

# Meson-based build for libpq only.
& $mesonExe @($mesonArgs + @(
    'setup', $env:CVC_BUILD_DIR,
    "--prefix=$env:CVC_INSTALL_DIR",
    '--buildtype=release',
    '--wrap-mode=nofallback',
    '-Dssl=openssl',
    '-Dzlib=enabled',
    '-Dreadline=disabled',
    '-Dzstd=disabled',
    '-Dlz4=disabled',
    '-Dnls=disabled',
    # Only libpq builds (below), but meson still configures the server: keep
    # the PL languages and TAP tests off, or it enables plpython against
    # whatever Python is on PATH (a hosted runner's own).  build.sh disables
    # plpython too.
    '-Dplperl=disabled',
    '-Dplpython=disabled',
    '-Dpltcl=disabled',
    '-Dtap_tests=disabled',
    # No GSSAPI on Windows: the krb5 recipe has no Windows build (recipe.yaml
    # declares it for the Unix platforms only), so meson found no gssapi and
    # stopped (populate job 22351).  Windows libpq authenticates Kerberos and
    # Negotiate through SSPI (secur32), which meson enables on its own.
    '-Dgssapi=disabled'
))
if ($LASTEXITCODE -ne 0) { throw "meson setup failed" }

Set-Location $env:CVC_BUILD_DIR
# Build libpq alone, then stage the client files the Unix 'ninja install'
# leaves after package.files filters it.  The whole project does not build
# here: the backend's postgres.def step needs a native perl (an MSYS perl on
# PATH turned dumpbin's /nologo into a path), and none of it ships.  The old
# `ninja 'src/interfaces/libpq:pq'` was meson-compile syntax, which ninja
# rejects ("unknown target"), and 'ninja install' builds every target.
& $mesonExe @($mesonArgs + @(
    'compile', '-C', $env:CVC_BUILD_DIR, '-j', $env:CVC_JOBS,
    'src/interfaces/libpq/libpq:shared_library',
    'src/interfaces/libpq/libpq:static_library'
))
if ($LASTEXITCODE -ne 0) { throw "meson compile failed" }

# Names under MSVC: default_lib_args sets name_prefix '', so the DLL is
# libpq.dll with import library libpq.lib; meson names static libraries
# lib*.a, here libpq.a.
$src = $env:CVC_SOURCE_DIR
$bld = $env:CVC_BUILD_DIR
$dst = $env:CVC_INSTALL_DIR
foreach ($d in 'bin', 'lib\pkgconfig', 'include\libpq') {
    New-Item -ItemType Directory -Force -Path (Join-Path $dst $d) | Out-Null
}
$pqBuild = Join-Path $bld 'src\interfaces\libpq'
Copy-Item (Join-Path $pqBuild 'libpq.dll') (Join-Path $dst 'bin')
Copy-Item (Join-Path $pqBuild 'libpq.lib') (Join-Path $dst 'lib')
Copy-Item (Join-Path $pqBuild 'libpq.a')   (Join-Path $dst 'lib')
Copy-Item (Join-Path $bld 'meson-private\libpq.pc') (Join-Path $dst 'lib\pkgconfig')
# The headers src/include/meson.build and src/interfaces/libpq/meson.build
# install into includedir (not the internal/server ones).
Copy-Item (Join-Path $src 'src\interfaces\libpq\libpq-fe.h'),
          (Join-Path $src 'src\interfaces\libpq\libpq-events.h'),
          (Join-Path $src 'src\include\postgres_ext.h'),
          (Join-Path $src 'src\include\pg_config_manual.h'),
          (Join-Path $bld 'src\include\pg_config.h'),
          (Join-Path $bld 'src\include\pg_config_os.h') (Join-Path $dst 'include')
Copy-Item (Join-Path $src 'src\include\libpq\libpq-fs.h') (Join-Path $dst 'include\libpq')

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

# libpq.pc carries the temporary install dir as prefix=; make it relocatable,
# as build.sh does with cvc_rewrite_install_paths.
Invoke-CvcRewriteInstallPaths
