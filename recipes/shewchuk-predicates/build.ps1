# recipes/shewchuk-predicates/build.ps1 — header-only: installs the generated
# include\shewchuk\predicates.hpp, the public-domain notice and a CMake package exporting
# Shewchuk::predicates, as build.sh does. No MSVC setup: there is no compiler call.
$ErrorActionPreference = 'Stop'

if (-not $env:CVC_SOURCE_DIR)  { throw 'CVC_SOURCE_DIR must be set' }
if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }

$src = $env:CVC_SOURCE_DIR
$inc = Join-Path $env:CVC_INSTALL_DIR 'include\shewchuk'
New-Item -ItemType Directory -Force -Path $inc | Out-Null
Copy-Item -Force (Join-Path $src 'include\shewchuk\predicates.hpp') $inc

$lic = Join-Path $env:CVC_INSTALL_DIR 'share\licenses\shewchuk-predicates'
New-Item -ItemType Directory -Force -Path $lic | Out-Null
Copy-Item -Force (Join-Path $src 'README.md') (Join-Path $lic 'README.md')
# The notice exactly as predicates.c carries it.
Get-Content (Join-Path $src 'predicates.c') -TotalCount 31 |
    Set-Content -Encoding ascii (Join-Path $lic 'NOTICE')

$cmakeDir = Join-Path $env:CVC_INSTALL_DIR 'lib\cmake\ShewchukPredicates'
New-Item -ItemType Directory -Force -Path $cmakeDir | Out-Null
$config = @'
# Shewchuk's predicates (header-only), as cvcpkg packages them: #include <shewchuk/predicates.hpp>.
get_filename_component(_shewchuk_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
if(NOT TARGET Shewchuk::predicates)
  add_library(Shewchuk::predicates INTERFACE IMPORTED)
  set_target_properties(Shewchuk::predicates PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_shewchuk_prefix}/include")
endif()
unset(_shewchuk_prefix)
'@
Set-Content -Encoding ascii -Path (Join-Path $cmakeDir 'ShewchukPredicatesConfig.cmake') -Value $config
$version = if ($env:CVC_VERSION) { $env:CVC_VERSION } else { '1996.05.18' }
$versionFile = @"
set(PACKAGE_VERSION "$version")
if(PACKAGE_FIND_VERSION VERSION_GREATER PACKAGE_VERSION)
  set(PACKAGE_VERSION_COMPATIBLE FALSE)
else()
  set(PACKAGE_VERSION_COMPATIBLE TRUE)
  if(PACKAGE_FIND_VERSION STREQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_EXACT TRUE)
  endif()
endif()
"@
Set-Content -Encoding ascii -Path (Join-Path $cmakeDir 'ShewchukPredicatesConfigVersion.cmake') -Value $versionFile

if (-not (Select-String -Quiet -SimpleMatch 'Placed in the public domain by' (Join-Path $lic 'NOTICE'))) {
    throw 'shewchuk-predicates: NOTICE is not predicates.c''s notice'
}
Write-Host "shewchuk-predicates: installed predicates.hpp -> $inc"
