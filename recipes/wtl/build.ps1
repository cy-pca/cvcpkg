# recipes/wtl/build.ps1 -- install WTL's headers (header-only), its licence and a CMake package.
$ErrorActionPreference = 'Stop'
foreach ($name in 'CVC_SOURCE_DIR', 'CVC_INSTALL_DIR') {
    if (-not [Environment]::GetEnvironmentVariable($name)) { throw "$name must be set" }
}
$src = Join-Path $env:CVC_SOURCE_DIR 'Include'
if (-not (Test-Path (Join-Path $src 'atlapp.h'))) { throw "WTL headers not found in $src" }

$inc = Join-Path $env:CVC_INSTALL_DIR 'include\wtl'
New-Item -ItemType Directory -Force -Path $inc | Out-Null
Copy-Item -Force (Join-Path $src '*.h') $inc

$lic = Join-Path $env:CVC_INSTALL_DIR 'share\licenses\wtl'
New-Item -ItemType Directory -Force -Path $lic | Out-Null
Copy-Item -Force (Join-Path $env:CVC_SOURCE_DIR 'MS-PL.txt') (Join-Path $lic 'LICENSE')

$cm = Join-Path $env:CVC_INSTALL_DIR 'share\cmake\WTL'
New-Item -ItemType Directory -Force -Path $cm | Out-Null
@'
# WTL (header-only). WTL::WTL adds <prefix>/include/wtl to the include path.
get_filename_component(_wtl_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
if (NOT TARGET WTL::WTL)
  add_library(WTL::WTL INTERFACE IMPORTED)
  set_target_properties(WTL::WTL PROPERTIES INTERFACE_INCLUDE_DIRECTORIES "${_wtl_prefix}/include/wtl")
endif ()
set(WTL_INCLUDE_DIRS "${_wtl_prefix}/include/wtl")
set(WTL_FOUND TRUE)
unset(_wtl_prefix)
'@ | Set-Content -Encoding ascii -LiteralPath (Join-Path $cm 'WTLConfig.cmake')
@'
set(PACKAGE_VERSION "10.0.10320")
if (PACKAGE_FIND_VERSION VERSION_GREATER PACKAGE_VERSION)
  set(PACKAGE_VERSION_COMPATIBLE FALSE)
else ()
  set(PACKAGE_VERSION_COMPATIBLE TRUE)
  if (PACKAGE_FIND_VERSION STREQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_EXACT TRUE)
  endif ()
endif ()
'@ | Set-Content -Encoding ascii -LiteralPath (Join-Path $cm 'WTLConfigVersion.cmake')
Write-Host "cvcpkg: wtl -> $inc"
