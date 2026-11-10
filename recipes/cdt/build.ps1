# recipes/cdt/build.ps1 — CDT is header-only: nothing compiles, so this installs the headers
# under include\CDT\, the licences, and a CMake package exporting CDT::CDT, as build.sh does
# (see there for why not CDT's own CMake install). No MSVC setup: there is no compiler call.
$ErrorActionPreference = 'Stop'

if (-not $env:CVC_SOURCE_DIR)  { throw 'CVC_SOURCE_DIR must be set' }
if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }

$src = Join-Path $env:CVC_SOURCE_DIR 'CDT'
$inc = Join-Path $env:CVC_INSTALL_DIR 'include\CDT'
New-Item -ItemType Directory -Force -Path $inc | Out-Null
Get-ChildItem (Join-Path $src 'include') -File | Where-Object { $_.Extension -in '.h', '.hpp' } |
    ForEach-Object { Copy-Item -Force $_.FullName $inc }

$lic = Join-Path $env:CVC_INSTALL_DIR 'share\licenses\cdt'
New-Item -ItemType Directory -Force -Path $lic | Out-Null
foreach ($f in @('LICENSE', 'LICENSE-predicates.txt', 'LICENSE-LLVM.txt')) {
    Copy-Item -Force (Join-Path $src $f) $lic
}

$cmakeDir = Join-Path $env:CVC_INSTALL_DIR 'lib\cmake\CDT'
New-Item -ItemType Directory -Force -Path $cmakeDir | Out-Null
$config = @'
# CDT (header-only), as cvcpkg packages it: the headers are in <prefix>/include/CDT/, so
# consumers write #include <CDT/CDT.h>.
get_filename_component(_cdt_prefix "${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)
if(NOT TARGET CDT::CDT)
  add_library(CDT::CDT INTERFACE IMPORTED)
  set_target_properties(CDT::CDT PROPERTIES
    INTERFACE_INCLUDE_DIRECTORIES "${_cdt_prefix}/include")
endif()
unset(_cdt_prefix)
'@
Set-Content -Encoding ascii -Path (Join-Path $cmakeDir 'CDTConfig.cmake') -Value $config
$version = if ($env:CVC_VERSION) { $env:CVC_VERSION } else { '2.0.1' }
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
Set-Content -Encoding ascii -Path (Join-Path $cmakeDir 'CDTConfigVersion.cmake') -Value $versionFile

# The patch must have applied (patches\no-mutable-statics.patch).
if (-not (Select-String -Quiet -SimpleMatch 'static const double log2_constant' (Join-Path $inc 'Triangulation.hpp'))) {
    throw 'cdt: Triangulation.hpp is not patched'
}
if (-not (Select-String -Quiet -SimpleMatch 'define PREDICATES_EPSILON(T) (std::numeric_limits<T>::epsilon() / T(2))' (Join-Path $inc 'predicates.h'))) {
    throw 'cdt: predicates.h is not patched'
}
Write-Host "cdt: installed headers -> $inc"
