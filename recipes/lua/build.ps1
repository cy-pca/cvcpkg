# recipes/lua/build.ps1 — build Lua from source on Windows using MSVC.
#
# Upstream Makefile has no Windows target; we compile the sources
# directly with cl.exe following the well-known recipe (all .c files
# under src/ except lua.c and luac.c into liblua.dll, then link lua.exe
# and luac.exe against it).
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\\_common\\env-windows.ps1"

$src = Join-Path $env:CVC_SOURCE_DIR 'src'
$build = $env:CVC_BUILD_DIR
$install = $env:CVC_INSTALL_DIR

New-Item -ItemType Directory -Force -Path $build | Out-Null
New-Item -ItemType Directory -Force -Path "$install\bin" | Out-Null
New-Item -ItemType Directory -Force -Path "$install\lib" | Out-Null
New-Item -ItemType Directory -Force -Path "$install\include" | Out-Null
New-Item -ItemType Directory -Force -Path "$install\lib\pkgconfig" | Out-Null

Push-Location $build
try {
    # All lib sources except the two application entry points.
    $libSources = Get-ChildItem "$src\*.c" -Exclude 'lua.c','luac.c' | ForEach-Object { $_.FullName }

    # Compile the DLL with LUA_BUILD_AS_DLL so the API symbols are
    # exported.  /MD -> link against the shared CRT.
    & cl /nologo /O2 /W3 /MD /DLUA_BUILD_AS_DLL /DLUA_COMPAT_5_3 `
        $libSources /link /DLL /IMPLIB:lua54.lib /OUT:lua54.dll
    if ($LASTEXITCODE -ne 0) { throw "liblua build failed" }

    # Interpreter and compiler executables.
    & cl /nologo /O2 /W3 /MD /DLUA_COMPAT_5_3 `
        "$src\lua.c" lua54.lib /Fe:lua.exe
    if ($LASTEXITCODE -ne 0) { throw "lua.exe build failed" }

    & cl /nologo /O2 /W3 /MD /DLUA_COMPAT_5_3 `
        $libSources "$src\luac.c" /Fe:luac.exe
    if ($LASTEXITCODE -ne 0) { throw "luac.exe build failed" }

    Copy-Item 'lua54.dll' "$install\bin\"
    Copy-Item 'lua54.lib' "$install\lib\"
    Copy-Item 'lua.exe'   "$install\bin\"
    Copy-Item 'luac.exe'  "$install\bin\"
} finally {
    Pop-Location
}

# Public headers.
Copy-Item "$src\lua.h"      "$install\include\"
Copy-Item "$src\luaconf.h"  "$install\include\"
Copy-Item "$src\lualib.h"   "$install\include\"
Copy-Item "$src\lauxlib.h"  "$install\include\"
Copy-Item "$src\lua.hpp"    "$install\include\"

# pkg-config file for MSYS2 / MinGW consumers.
$pc = @'
prefix=${pcfiledir}/../..
exec_prefix=${prefix}
libdir=${exec_prefix}/lib
includedir=${prefix}/include

Name: Lua
Description: An extensible embeddable language
URL: https://www.lua.org/
Version: 5.4.7
Libs: -L${libdir} -llua54
Cflags: -I${includedir}
'@
Set-Content -NoNewline -Path "$install\lib\pkgconfig\lua5.4.pc" -Value $pc
Copy-Item "$install\lib\pkgconfig\lua5.4.pc" "$install\lib\pkgconfig\lua.pc"

if (Get-Command Invoke-CvcRewriteInstallPaths -ErrorAction SilentlyContinue) {
    Invoke-CvcRewriteInstallPaths
}
