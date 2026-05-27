# recipes/clapack/build-wasm.ps1 — cross-compile CLAPACK to wasm.
# CLAPACK is a C translation of reference LAPACK (f2c'd).
# Provides BLAS + LAPACK for wasm targets.
# Note: CLAPACK 3.2.1's cmake has no install() rules, so we manually
# install libraries, headers, and a cmake config package.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-wasm.ps1"
# CLAPACK 3.2.1 unconditionally does add_subdirectory(TESTING) — the test
# executables can't link under emscripten. Remove the directory so cmake skips it.
if (Test-Path "$env:CVC_SOURCE_DIR\TESTING") {
    Remove-Item -Recurse -Force "$env:CVC_SOURCE_DIR\TESTING"
}
$allArgs = @(
    '-G', 'Ninja',
    '-S', $env:CVC_SOURCE_DIR,
    '-B', $env:CVC_BUILD_DIR,
    "-DCMAKE_BUILD_TYPE=$cmakeBuildType",
    '-DCMAKE_POSITION_INDEPENDENT_CODE=ON',
    '-DCMAKE_POLICY_VERSION_MINIMUM=3.5',
    "-DCMAKE_TOOLCHAIN_FILE=$emscriptenToolchain",
    '-DCMAKE_C_FLAGS=-Wno-implicit-function-declaration',
    '-DBUILD_TESTING=OFF'
)

& cmake @allArgs
if ($LASTEXITCODE -ne 0) { throw "cmake configure failed" }

& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw "cmake build failed" }

# Manual install — CLAPACK's cmake has no install() commands.
$libDir = "$env:CVC_INSTALL_DIR\lib"
$incDir = "$env:CVC_INSTALL_DIR\include"
$cmakeDir = "$env:CVC_INSTALL_DIR\lib\cmake\clapack"
New-Item -ItemType Directory -Force -Path $libDir | Out-Null
New-Item -ItemType Directory -Force -Path $incDir | Out-Null
New-Item -ItemType Directory -Force -Path $cmakeDir | Out-Null

# Libraries
Copy-Item "$env:CVC_BUILD_DIR\F2CLIBS\libf2c\f2c.lib" "$libDir\f2c.lib" -ErrorAction SilentlyContinue
Copy-Item "$env:CVC_BUILD_DIR\F2CLIBS\libf2c\libf2c.a" "$libDir\libf2c.a" -ErrorAction SilentlyContinue
Copy-Item "$env:CVC_BUILD_DIR\BLAS\SRC\blas.lib" "$libDir\blas.lib" -ErrorAction SilentlyContinue
Copy-Item "$env:CVC_BUILD_DIR\BLAS\SRC\libblas.a" "$libDir\libblas.a" -ErrorAction SilentlyContinue
Copy-Item "$env:CVC_BUILD_DIR\SRC\lapack.lib" "$libDir\lapack.lib" -ErrorAction SilentlyContinue
Copy-Item "$env:CVC_BUILD_DIR\SRC\liblapack.a" "$libDir\liblapack.a" -ErrorAction SilentlyContinue

# Headers
Copy-Item "$env:CVC_SOURCE_DIR\INCLUDE\blaswrap.h" $incDir
Copy-Item "$env:CVC_SOURCE_DIR\INCLUDE\clapack.h" $incDir
Copy-Item "$env:CVC_SOURCE_DIR\INCLUDE\f2c.h" $incDir

# Generate cmake config package so find_package(clapack CONFIG) works.
$configContent = @'
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
'@
Set-Content -Path "$cmakeDir\clapack-config.cmake" -Value $configContent

$versionContent = @'
set(PACKAGE_VERSION "3.2.1")
if(NOT ${PACKAGE_FIND_VERSION} VERSION_GREATER ${PACKAGE_VERSION})
  set(PACKAGE_VERSION_COMPATIBLE 1)
  if(${PACKAGE_FIND_VERSION} VERSION_EQUAL ${PACKAGE_VERSION})
    set(PACKAGE_VERSION_EXACT 1)
  endif()
endif()
'@
Set-Content -Path "$cmakeDir\clapack-config-version.cmake" -Value $versionContent

