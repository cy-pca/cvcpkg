# recipes/clapack/build.ps1 — build CLAPACK from the netlib CMake
# tarball on Windows with MSVC.
#
# CLAPACK is the f2c-translated C version of reference LAPACK 3.2.1;
# it ships a CMakeLists.txt (the "-CMAKE" flavour of the tarball) but
# has no install() rules and unconditionally adds the TESTING
# subdirectory.  We patch out TESTING, drive the build, then stage the
# resulting f2c.lib / blas.lib / lapack.lib plus INCLUDE/*.h into
# $CVC_INSTALL_DIR and hand-write a CMake config package so
# find_package(clapack CONFIG) works for downstream consumers (levmar).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# CLAPACK 3.2.1's CMakeLists.txt unconditionally does
# add_subdirectory(TESTING).  The test executables need extra munging
# on non-Unix and are not required for our downstream consumers.
$cmakeLists = Join-Path $env:CVC_SOURCE_DIR 'CMakeLists.txt'
if (Test-Path $cmakeLists) {
    (Get-Content $cmakeLists) `
        | Where-Object { $_ -notmatch 'add_subdirectory\s*\(\s*TESTING\s*\)' } `
        | Set-Content $cmakeLists
}

# CLAPACK 3.2.1 is f2c output from 2008 with C89-style implicit
# function declarations and int/pointer mismatches.  MSVC treats these
# as warnings by default (level 4), but /WX-clean is not the goal —
# suppress the common noise and disable the auto-generated /W3 warning
# flood that would balloon the log.
Invoke-CvcCMakeBuild @(
    '-DBUILD_TESTING=OFF',
    '-DCMAKE_C_FLAGS=/wd4013 /wd4133 /wd4244 /wd4267 /wd4996 /D_CRT_SECURE_NO_WARNINGS'
)

# CLAPACK's cmake has no install() rules; stage manually.
$installLib     = Join-Path $env:CVC_INSTALL_DIR 'lib'
$installInclude = Join-Path $env:CVC_INSTALL_DIR 'include'
$installCmake   = Join-Path $env:CVC_INSTALL_DIR 'lib\cmake\clapack'
foreach ($d in @($installLib, $installInclude, $installCmake)) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

# Libraries — MSVC + Ninja single-config drops .lib files at the
# source-dir-mirrored paths under the build tree.
$libSuffix = if ($env:CVC_LINK -eq 'static') { 'lib' } else { 'dll' }
foreach ($libInfo in @(
    @{ Target = 'f2c';    Src = 'F2CLIBS\libf2c' },
    @{ Target = 'blas';   Src = 'BLAS\SRC' },
    @{ Target = 'lapack'; Src = 'SRC' }
)) {
    $srcLib = Join-Path $env:CVC_BUILD_DIR ("$($libInfo.Src)\$($libInfo.Target).lib")
    if (-not (Test-Path $srcLib)) {
        throw "expected library not found: $srcLib"
    }
    Copy-Item -Force $srcLib $installLib
    # Shared: also grab the .dll (goes to bin/ on Windows).
    if ($env:CVC_LINK -ne 'static') {
        $srcDll = Join-Path $env:CVC_BUILD_DIR ("$($libInfo.Src)\$($libInfo.Target).dll")
        if (Test-Path $srcDll) {
            $installBin = Join-Path $env:CVC_INSTALL_DIR 'bin'
            New-Item -ItemType Directory -Force -Path $installBin | Out-Null
            Copy-Item -Force $srcDll $installBin
        }
    }
}

# Headers.
foreach ($h in @('blaswrap.h','clapack.h','f2c.h')) {
    $srcH = Join-Path $env:CVC_SOURCE_DIR ("INCLUDE\$h")
    if (Test-Path $srcH) {
        Copy-Item -Force $srcH $installInclude
    }
}

# CMake config package so find_package(clapack CONFIG) works.
$libtype = if ($env:CVC_LINK -eq 'static') { 'STATIC' } else { 'SHARED' }
$configContent = @"
get_filename_component(_clapack_prefix "`${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

if(NOT TARGET f2c)
  add_library(f2c $libtype IMPORTED)
  set_target_properties(f2c PROPERTIES
    IMPORTED_LOCATION "`${_clapack_prefix}/lib/f2c.lib"
    INTERFACE_INCLUDE_DIRECTORIES "`${_clapack_prefix}/include"
  )
endif()

if(NOT TARGET blas)
  add_library(blas $libtype IMPORTED)
  set_target_properties(blas PROPERTIES
    IMPORTED_LOCATION "`${_clapack_prefix}/lib/blas.lib"
    INTERFACE_LINK_LIBRARIES "f2c"
  )
endif()

if(NOT TARGET lapack)
  add_library(lapack $libtype IMPORTED)
  set_target_properties(lapack PROPERTIES
    IMPORTED_LOCATION "`${_clapack_prefix}/lib/lapack.lib"
    INTERFACE_INCLUDE_DIRECTORIES "`${_clapack_prefix}/include"
    INTERFACE_LINK_LIBRARIES "blas;f2c"
  )
endif()

set(clapack_FOUND TRUE)
"@
Set-Content -Encoding UTF8 -Path (Join-Path $installCmake 'clapack-config.cmake') -Value $configContent

$versionContent = @'
set(PACKAGE_VERSION "3.2.1")
if(NOT ${PACKAGE_FIND_VERSION} VERSION_GREATER ${PACKAGE_VERSION})
  set(PACKAGE_VERSION_COMPATIBLE 1)
  if(${PACKAGE_FIND_VERSION} VERSION_EQUAL ${PACKAGE_VERSION})
    set(PACKAGE_VERSION_EXACT 1)
  endif()
endif()
'@
Set-Content -Encoding UTF8 -Path (Join-Path $installCmake 'clapack-config-version.cmake') -Value $versionContent

Write-Host "cvcpkg: clapack staged to $env:CVC_INSTALL_DIR"
