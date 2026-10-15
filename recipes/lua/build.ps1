# recipes/lua/build.ps1 — build Lua from source on Windows using MSVC.
#
# Upstream Makefile has no MSVC target; we compile the sources directly with
# cl.exe, the Windows analog of build.sh:
#
#   bin\lua54.dll + lib\lua54.lib     the shared library and its import library
#                                     (all src\*.c except lua.c/luac.c, built
#                                     with LUA_BUILD_AS_DLL so LUA_API exports)
#   lib\lua54_static.lib              the same sources as a static library
#   bin\lua.exe                       linked against lua54.dll
#   bin\luac.exe                      linked statically -- it calls Lua-internal
#                                     functions the DLL does not export
#
# plus lib\pkgconfig\lua5.4.pc (MSYS2/MinGW-style consumers) and the CMake
# package lib\cmake\lua\lua-config.cmake: lua::lua is the DLL (IMPORTED_IMPLIB
# lua54.lib, LUA_BUILD_AS_DLL on the interface so LUA_API means dllimport),
# lua::lua_static the static library.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$src = Join-Path $env:CVC_SOURCE_DIR 'src'
$build = $env:CVC_BUILD_DIR
$install = $env:CVC_INSTALL_DIR

# Version from src\lua.h -- the same source of truth lua-common.sh uses on Unix.
$luaH = Get-Content -Raw -LiteralPath (Join-Path $src 'lua.h')
function Get-LuaVersionPart([string]$name) {
    if ($luaH -match "#define\s+$name\s+`"(\d+)`"") { return $Matches[1] }
    throw "lua: $name not found in src\lua.h"
}
$maj = Get-LuaVersionPart 'LUA_VERSION_MAJOR'
$min = Get-LuaVersionPart 'LUA_VERSION_MINOR'
$rel = Get-LuaVersionPart 'LUA_VERSION_RELEASE'
$luaV = "$maj.$min"
$luaR = "$maj.$min.$rel"
$dllBase = "lua$maj$min"            # lua54
if ($env:CVC_VERSION -and $env:CVC_VERSION -ne $luaR) {
    throw "lua: recipe upstream_version $($env:CVC_VERSION) != src\lua.h $luaR"
}
Write-Output "lua: version $luaR"

$dllDir = Join-Path $build 'dll'
$staticDir = Join-Path $build 'static'
foreach ($d in @($dllDir, $staticDir, "$install\bin", "$install\lib", "$install\include",
                 "$install\lib\pkgconfig", "$install\lib\cmake\lua")) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}

# All lib sources except the two application entry points.
$libSources = Get-ChildItem "$src\*.c" -Exclude 'lua.c','luac.c' | ForEach-Object { $_.FullName }

# Shared: lua54.dll + import library, and lua.exe against it.  /MD -> the
# shared CRT, as every other cvcpkg Windows bundle.
Push-Location $dllDir
try {
    & cl /nologo /O2 /W3 /MD /DLUA_BUILD_AS_DLL /DLUA_COMPAT_5_3 `
        $libSources /link /DLL "/IMPLIB:$dllBase.lib" "/OUT:$dllBase.dll"
    if ($LASTEXITCODE -ne 0) { throw "$dllBase.dll build failed" }

    & cl /nologo /O2 /W3 /MD /DLUA_BUILD_AS_DLL /DLUA_COMPAT_5_3 `
        "$src\lua.c" "$dllBase.lib" /Fe:lua.exe
    if ($LASTEXITCODE -ne 0) { throw "lua.exe build failed" }

    Copy-Item "$dllBase.dll" "$install\bin\"
    Copy-Item "$dllBase.lib" "$install\lib\"
    Copy-Item 'lua.exe'      "$install\bin\"
} finally {
    Pop-Location
}

# Static: lua54_static.lib (no LUA_BUILD_AS_DLL -> plain extern LUA_API), and
# luac.exe linked against it.
Push-Location $staticDir
try {
    & cl /nologo /O2 /W3 /MD /DLUA_COMPAT_5_3 /c $libSources
    if ($LASTEXITCODE -ne 0) { throw "static lua objects failed" }
    $objs = Get-ChildItem -Filter '*.obj' | ForEach-Object { $_.Name }
    & lib /nologo "/OUT:${dllBase}_static.lib" @objs
    if ($LASTEXITCODE -ne 0) { throw "${dllBase}_static.lib build failed" }

    & cl /nologo /O2 /W3 /MD /DLUA_COMPAT_5_3 `
        "$src\luac.c" "${dllBase}_static.lib" /Fe:luac.exe
    if ($LASTEXITCODE -ne 0) { throw "luac.exe build failed" }

    Copy-Item "${dllBase}_static.lib" "$install\lib\"
    Copy-Item 'luac.exe'              "$install\bin\"
} finally {
    Pop-Location
}

# Public headers.
foreach ($h in @('lua.h', 'luaconf.h', 'lualib.h', 'lauxlib.h', 'lua.hpp')) {
    Copy-Item (Join-Path $src $h) "$install\include\"
}

# pkg-config file for MSYS2 / MinGW consumers (links the import library).
$pc = @"
V=$luaV
R=$luaR
prefix=`${pcfiledir}/../..
exec_prefix=`${prefix}
libdir=`${exec_prefix}/lib
includedir=`${prefix}/include

Name: Lua
Description: An extensible embeddable language
URL: https://www.lua.org/
Version: $luaR
Libs: -L`${libdir} -l$dllBase
Cflags: -I`${includedir}

"@
$pcDir = "$install\lib\pkgconfig"
Set-Content -NoNewline -Encoding ascii -Path "$pcDir\lua$luaV.pc" -Value $pc
foreach ($alias in @('lua.pc', "lua-$luaV.pc", "lua$maj$min.pc")) {
    Copy-Item "$pcDir\lua$luaV.pc" "$pcDir\$alias"
}

# CMake package -- same targets and version rule as lua-common.sh writes on Unix.
$config = @"
# lua-config.cmake -- CMake package for Lua $luaR (Windows/MSVC), written by the
# cvcpkg lua recipe (recipes/lua/build.ps1); Lua upstream ships no CMake package.
#
#   find_package(lua $luaV CONFIG REQUIRED)
#   target_link_libraries(app PRIVATE lua::lua)          # $dllBase.dll via $dllBase.lib
#   target_link_libraries(app PRIVATE lua::lua_static)   # ${dllBase}_static.lib
#
# $dllBase.dll lives in bin\ next to lua.exe: put <prefix>\bin on PATH (or copy
# the DLL next to your executable) to run against lua::lua. Every path below is
# derived from this file's own location, so the prefix stays relocatable.
get_filename_component(_lua_prefix "`${CMAKE_CURRENT_LIST_DIR}/../../.." ABSOLUTE)

set(LUA_VERSION_STRING "$luaR")
set(LUA_VERSION_MAJOR $maj)
set(LUA_VERSION_MINOR $min)
set(LUA_VERSION_PATCH $rel)
set(LUA_INCLUDE_DIR "`${_lua_prefix}/include")

if(NOT TARGET lua::lua_static)
  add_library(lua::lua_static STATIC IMPORTED)
  set_target_properties(lua::lua_static PROPERTIES
    IMPORTED_LOCATION "`${_lua_prefix}/lib/${dllBase}_static.lib"
    IMPORTED_LINK_INTERFACE_LANGUAGES "C"
    INTERFACE_INCLUDE_DIRECTORIES "`${_lua_prefix}/include")
endif()

if(NOT TARGET lua::lua)
  add_library(lua::lua SHARED IMPORTED)
  set_target_properties(lua::lua PROPERTIES
    IMPORTED_LOCATION "`${_lua_prefix}/bin/$dllBase.dll"
    IMPORTED_IMPLIB "`${_lua_prefix}/lib/$dllBase.lib"
    INTERFACE_INCLUDE_DIRECTORIES "`${_lua_prefix}/include"
    INTERFACE_COMPILE_DEFINITIONS "LUA_BUILD_AS_DLL")
endif()

set(LUA_LIBRARY lua::lua)
set(LUA_LIBRARIES lua::lua)
set(LUA_FOUND TRUE)
unset(_lua_prefix)

"@
$version = @"
# lua-config-version.cmake -- written by the cvcpkg lua recipe. Lua $luaV.x
# is ABI-compatible only within the $luaV series (SameMinorVersion).
set(PACKAGE_VERSION "$luaR")
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
elseif(PACKAGE_FIND_VERSION_MAJOR STREQUAL "$maj" AND PACKAGE_FIND_VERSION_MINOR STREQUAL "$min")
  set(PACKAGE_VERSION_COMPATIBLE TRUE)
  if(PACKAGE_FIND_VERSION STREQUAL PACKAGE_VERSION)
    set(PACKAGE_VERSION_EXACT TRUE)
  endif()
else()
  set(PACKAGE_VERSION_COMPATIBLE FALSE)
endif()

"@
Set-Content -NoNewline -Encoding ascii -Path "$install\lib\cmake\lua\lua-config.cmake" -Value $config
Set-Content -NoNewline -Encoding ascii -Path "$install\lib\cmake\lua\lua-config-version.cmake" -Value $version

if (Get-Command Invoke-CvcRewriteInstallPaths -ErrorAction SilentlyContinue) {
    Invoke-CvcRewriteInstallPaths
}

# The interpreter must run against the DLL it was linked to (bin\ is its own dir).
& "$install\bin\lua.exe" -e "assert(_VERSION == 'Lua $luaV'); print(_VERSION .. ' (dll) OK')"
if ($LASTEXITCODE -ne 0) { throw "lua.exe smoke check failed" }
