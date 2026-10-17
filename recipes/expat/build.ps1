# recipes/expat/build.ps1 — build Expat from source on Windows via CMake + MSVC.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

# EXPAT_BUILD_PKGCONFIG defaults OFF under MSVC (and ON everywhere else), so
# the Windows bundle shipped no expat.pc although package.files declares
# lib/pkgconfig/expat.pc. Consumers' pkg-config lookups (fontconfig's meson)
# then found expat only through its CMake package. The .pc names the import
# library (-llibexpat, -llibexpatMD for static), and Invoke-CvcCMakeBuild's
# Invoke-CvcRewriteInstallPaths turns its prefix= into ${pcfiledir}/../..
Invoke-CvcCMakeBuild @(
    '-DEXPAT_BUILD_TESTS=OFF',
    '-DEXPAT_BUILD_EXAMPLES=OFF',
    '-DEXPAT_BUILD_TOOLS=ON',
    '-DEXPAT_BUILD_DOCS=OFF',
    '-DEXPAT_BUILD_PKGCONFIG=ON'
)

$pc = Join-Path $env:CVC_INSTALL_DIR 'lib\pkgconfig\expat.pc'
if (-not (Test-Path -LiteralPath $pc)) { throw "expat: $pc was not installed" }
