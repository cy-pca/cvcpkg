# recipes/libpng/build.ps1 — build libpng from source on Windows via CMake + MSVC.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DPNG_TESTS=OFF',
    '-DPNG_TOOLS=OFF',
    "-DCMAKE_PREFIX_PATH=$env:CVC_DEPS_PREFIX"
)

# libpng's CMake installs libpng.pc / libpng16.pc everywhere except MSVC.
# freetype2.pc has "Requires.private: zlib, libpng", so without them pkg-config
# rejects freetype2 on Windows, and fontconfig's, cairo's and harfbuzz's meson
# fell back to downloading and building their own freetype.  Write them here,
# naming the import (or static) library this build actually installed.
$install = $env:CVC_INSTALL_DIR
$pngH = Get-Content -Raw -LiteralPath (Join-Path $install 'include\png.h')
if ($pngH -notmatch '#define\s+PNG_LIBPNG_VER_STRING\s+"([^"]+)"') {
    throw 'libpng: PNG_LIBPNG_VER_STRING not found in png.h'
}
$version = $Matches[1]
$wantStatic = $env:CVC_LINK -eq 'static'
$lib = Get-ChildItem (Join-Path $install 'lib\libpng16*.lib') |
    Where-Object { ($_.BaseName -like '*_static*') -eq $wantStatic } |
    Select-Object -First 1
if (-not $lib) { throw "libpng: no $(if ($wantStatic) { 'static' } else { 'import' }) library in lib\" }

$pc = @"
prefix=`${pcfiledir}/../..
exec_prefix=`${prefix}
libdir=`${exec_prefix}/lib
includedir=`${prefix}/include/libpng16

Name: libpng
Description: Loads and saves PNG files
Version: $version
Requires.private: zlib
Libs: -L`${libdir} -l$($lib.BaseName)
Cflags: -I`${includedir}
"@
New-Item -ItemType Directory -Force -Path "$install\lib\pkgconfig" | Out-Null
foreach ($name in @('libpng16.pc', 'libpng.pc')) {
    Set-Content -NoNewline -Encoding ascii -Path "$install\lib\pkgconfig\$name" -Value ($pc + "`n")
}
