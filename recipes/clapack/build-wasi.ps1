# recipes/clapack/build-wasi.ps1 — cross-compile CLAPACK to wasm32-wasi via wasi-sdk on Windows.
# CLAPACK 3.2.1's cmake has no install() rules; we install libs, headers,
# and a cmake config package by hand (mirrors build-wasm.ps1).
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasi.ps1"

# CLAPACK 3.2.1 unconditionally does add_subdirectory(TESTING) — the
# test executables can't link under wasi-libc.
$cmakeLists = Join-Path $env:CVC_SOURCE_DIR 'CMakeLists.txt'
(Get-Content $cmakeLists) | Where-Object { $_ -notmatch 'add_subdirectory\(TESTING\)' } |
    Set-Content $cmakeLists

Invoke-CvcWasiCMakeBuild -ExtraArgs @(
    '-DBUILD_TESTING=OFF',
    '-DCMAKE_C_FLAGS=-Wno-implicit-function-declaration'
)

# The upstream CMakeLists has no install() rules — copy artefacts by hand.
$installDir = $env:CVC_INSTALL_DIR
$buildDir   = $env:CVC_BUILD_DIR
$sourceDir  = $env:CVC_SOURCE_DIR

New-Item -ItemType Directory -Force -Path "$installDir\lib" | Out-Null
New-Item -ItemType Directory -Force -Path "$installDir\include" | Out-Null
New-Item -ItemType Directory -Force -Path "$installDir\lib\cmake\clapack" | Out-Null

Copy-Item -Force "$buildDir\F2CLIBS\libf2c\libf2c.a" "$installDir\lib\"
Copy-Item -Force "$buildDir\BLAS\SRC\libblas.a"     "$installDir\lib\"
Copy-Item -Force "$buildDir\SRC\liblapack.a"        "$installDir\lib\"

Copy-Item -Force "$sourceDir\INCLUDE\blaswrap.h" "$installDir\include\"
Copy-Item -Force "$sourceDir\INCLUDE\clapack.h"  "$installDir\include\"
Copy-Item -Force "$sourceDir\INCLUDE\f2c.h"      "$installDir\include\"

@'
get_filename_component(_clapack_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

if(NOT TARGET f2c)
  add_library(f2c STATIC IMPORTED)
  set_target_properties(f2c PROPERTIES
    IMPORTED_LOCATION "${_clapack_prefix}/lib/libf2c.a"
    INTERFACE_INCLUDE_DIRECTORIES "${_clapack_prefix}/include"
  )
endif()

if(NOT TARGET blas)
  add_library(blas STATIC IMPORTED)
  set_target_properties(blas PROPERTIES
    IMPORTED_LOCATION "${_clapack_prefix}/lib/libblas.a"
    INTERFACE_LINK_LIBRARIES "f2c"
  )
endif()

if(NOT TARGET lapack)
  add_library(lapack STATIC IMPORTED)
  set_target_properties(lapack PROPERTIES
    IMPORTED_LOCATION "${_clapack_prefix}/lib/liblapack.a"
    INTERFACE_INCLUDE_DIRECTORIES "${_clapack_prefix}/include"
    INTERFACE_LINK_LIBRARIES "blas;f2c"
  )
endif()

set(clapack_FOUND TRUE)
'@ | Set-Content "$installDir\lib\cmake\clapack\clapack-config.cmake"

@'
set(PACKAGE_VERSION "3.2.1")
if(NOT ${PACKAGE_FIND_VERSION} VERSION_GREATER ${PACKAGE_VERSION})
  set(PACKAGE_VERSION_COMPATIBLE 1)
  if(${PACKAGE_FIND_VERSION} VERSION_EQUAL ${PACKAGE_VERSION})
    set(PACKAGE_VERSION_EXACT 1)
  endif()
endif()
'@ | Set-Content "$installDir\lib\cmake\clapack\clapack-config-version.cmake"
