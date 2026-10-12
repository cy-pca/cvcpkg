# recipes/luajit/build.ps1 — build LuaJIT 2.1 on Windows with upstream's own
# src\msvcbuild.bat (MSVC), in its default dynamic mode:
#
#   bin\lua51.dll + lib\lua51.lib   the shared library and its import library
#   bin\luajit.exe                  the interpreter, linked against lua51.dll
#   bin\lua\jit\*.lua               the jit.* modules where luajit.exe looks for
#                                   them (package.path "!\lua\?.lua", ! = the
#                                   exe's directory -- relocatable as is)
#   include\luajit-2.1\*.h, lib\pkgconfig\luajit.pc, lib\cmake\luajit\
#
# No static library on Windows: msvcbuild.bat's `static` mode writes a
# different lua51.lib under the same name, and upstream warns that a static
# LuaJIT cannot load C modules (they bind to lua51.dll). luajit::luajit_static
# is therefore Unix-only.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"    # imports the MSVC developer environment

$srcRoot = $env:CVC_SOURCE_DIR
$src = Join-Path $srcRoot 'src'
$install = $env:CVC_INSTALL_DIR

# Version: 2.1.<commit time>, from the .relver file the GitHub archive carries.
$relver = (Get-Content -Raw -LiteralPath (Join-Path $srcRoot '.relver')).Trim()
if ($relver -notmatch '^\d+$') { throw "luajit: .relver is '$relver', not a commit timestamp" }
$mm = '2.1'
$version = "$mm.$relver"
if ($env:CVC_VERSION -and $env:CVC_VERSION -ne $version) {
    throw "luajit: recipe upstream_version $($env:CVC_VERSION) != source version $version"
}
Write-Output "luajit: version $version"

Push-Location $src
try {
    & cmd.exe /c msvcbuild.bat
    if ($LASTEXITCODE -ne 0) { throw "msvcbuild.bat failed ($LASTEXITCODE)" }
    foreach ($f in @('lua51.dll', 'lua51.lib', 'luajit.exe')) {
        if (-not (Test-Path -LiteralPath $f)) { throw "msvcbuild.bat did not produce $f" }
    }
} finally {
    Pop-Location
}

$inc = Join-Path $install "include\luajit-$mm"
$jitDir = Join-Path $install 'bin\lua\jit'
foreach ($d in @("$install\bin", "$install\lib", $inc, $jitDir,
                 "$install\lib\pkgconfig", "$install\lib\cmake\luajit")) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
Copy-Item (Join-Path $src 'luajit.exe') "$install\bin\"
Copy-Item (Join-Path $src 'lua51.dll')  "$install\bin\"
Copy-Item (Join-Path $src 'lua51.lib')  "$install\lib\"
foreach ($h in @('lua.h', 'lualib.h', 'lauxlib.h', 'luaconf.h', 'lua.hpp', 'luajit.h')) {
    Copy-Item (Join-Path $src $h) $inc
}
Copy-Item (Join-Path $src 'jit\*.lua') $jitDir

$pc = @"
majver=2
minver=1
relver=$relver
version=`${majver}.`${minver}.`${relver}
abiver=5.1

prefix=`${pcfiledir}/../..
exec_prefix=`${prefix}
libdir=`${exec_prefix}/lib
includedir=`${prefix}/include/luajit-`${majver}.`${minver}

Name: LuaJIT
Description: Just-in-time compiler for Lua
URL: https://luajit.org
Version: `${version}
Libs: -L`${libdir} -llua51
Cflags: -I`${includedir}

"@
Set-Content -NoNewline -Encoding ascii -Path "$install\lib\pkgconfig\luajit.pc" -Value $pc

$config = @"
# luajit-config.cmake -- CMake package for LuaJIT $version (Windows/MSVC),
# written by the cvcpkg luajit recipe (recipes/luajit/build.ps1).
#
#   find_package(luajit $mm CONFIG REQUIRED)
#   target_link_libraries(app PRIVATE luajit::luajit)   # lua51.dll via lua51.lib
#
# lua51.dll lives in bin\: put <prefix>\bin on PATH (or copy the DLL next to
# your executable) to run. No luajit::luajit_static on Windows.
get_filename_component(_luajit_prefix "`${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

set(LUAJIT_VERSION_STRING "$version")
set(LUAJIT_INCLUDE_DIR "`${_luajit_prefix}/include/luajit-$mm")

if(NOT TARGET luajit::luajit)
  add_library(luajit::luajit SHARED IMPORTED)
  set_target_properties(luajit::luajit PROPERTIES
    IMPORTED_LOCATION "`${_luajit_prefix}/bin/lua51.dll"
    IMPORTED_IMPLIB "`${_luajit_prefix}/lib/lua51.lib"
    INTERFACE_INCLUDE_DIRECTORIES "`${_luajit_prefix}/include/luajit-$mm"
    INTERFACE_COMPILE_DEFINITIONS "LUA_BUILD_AS_DLL")
endif()

set(LUAJIT_LIBRARIES luajit::luajit)
set(LUAJIT_FOUND TRUE)
unset(_luajit_prefix)

"@
$versionFile = @"
# luajit-config-version.cmake -- written by the cvcpkg luajit recipe.
set(PACKAGE_VERSION "$version")
if(NOT PACKAGE_FIND_VERSION)
  set(PACKAGE_VERSION_COMPATIBLE TRUE)
elseif(PACKAGE_FIND_VERSION_RANGE)
  if((PACKAGE_FIND_VERSION_RANGE_MIN STREQUAL "INCLUDE" AND PACKAGE_VERSION VERSION_LESS PACKAGE_FIND_VERSION_MIN)
     OR (PACKAGE_FIND_VERSION_RANGE_MAX STREQUAL "INCLUDE" AND PACKAGE_VERSION VERSION_GREATER PACKAGE_FIND_VERSION_MAX)
     OR (PACKAGE_FIND_VERSION_RANGE_MAX STREQUAL "EXCLUDE" AND PACKAGE_VERSION VERSION_GREATER_EQUAL PACKAGE_FIND_VERSION_MAX))
    set(PACKAGE_VERSION_COMPATIBLE FALSE)
  else()
    set(PACKAGE_VERSION_COMPATIBLE TRUE)
  endif()
elseif(PACKAGE_VERSION VERSION_LESS PACKAGE_FIND_VERSION)
  set(PACKAGE_VERSION_COMPATIBLE FALSE)
elseif(PACKAGE_FIND_VERSION_MAJOR STREQUAL "2" AND PACKAGE_FIND_VERSION_MINOR STREQUAL "1")
  set(PACKAGE_VERSION_COMPATIBLE TRUE)
  if(PACKAGE_FIND_VERSION STREQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_EXACT TRUE)
  endif()
else()
  set(PACKAGE_VERSION_COMPATIBLE FALSE)
endif()

"@
Set-Content -NoNewline -Encoding ascii -Path "$install\lib\cmake\luajit\luajit-config.cmake" -Value $config
Set-Content -NoNewline -Encoding ascii -Path "$install\lib\cmake\luajit\luajit-config-version.cmake" -Value $versionFile

if (Get-Command Invoke-CvcRewriteInstallPaths -ErrorAction SilentlyContinue) {
    Invoke-CvcRewriteInstallPaths
}

# Smoke: the interpreter runs against its DLL, and finds jit.bc in bin\lua\jit.
& "$install\bin\luajit.exe" -e "assert(jit.status()); print(jit.version .. ' OK')"
if ($LASTEXITCODE -ne 0) { throw "luajit.exe smoke check failed" }
& "$install\bin\luajit.exe" -bl -e "local x = 1" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "luajit.exe -bl (jit.* modules) failed" }
