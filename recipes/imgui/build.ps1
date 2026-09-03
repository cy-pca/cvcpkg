# recipes/imgui/build.ps1 — compile Dear ImGui's core into imgui.lib (MSVC) and
# install headers + backend sources. The Windows analog of build.sh; ImGui ships
# no CMake install, so we compile the core translation units by hand with cl.exe.
#
# Static imgui.lib on purpose: ImGui decorates no symbols with __declspec(dllexport),
# so a Windows DLL would export nothing and downstream links would fail. Consumers
# (e.g. cvcGL) link this static lib directly. The backends/ tree ships as SOURCE
# (headers + .cpp) — the consumer compiles the platform/renderer glue it needs.
$ErrorActionPreference = 'Stop'

if (-not $env:CVC_SOURCE_DIR) { throw 'CVC_SOURCE_DIR must be set' }
if (-not $env:CVC_INSTALL_DIR) { throw 'CVC_INSTALL_DIR must be set' }
if (-not $env:CVC_BUILD_DIR) { $env:CVC_BUILD_DIR = $env:CVC_SOURCE_DIR }

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1" # imports MSVC (cl.exe/lib.exe), strips MinGW from PATH

Set-Location $env:CVC_SOURCE_DIR

$lib = Join-Path $env:CVC_INSTALL_DIR 'lib'
$incRoot = Join-Path $env:CVC_INSTALL_DIR 'include\imgui'
$incBackends = Join-Path $incRoot 'backends'
New-Item -ItemType Directory -Force -Path $lib, $incRoot, $incBackends | Out-Null

# MSVC runtime: match the link mode (static -> /MT, shared -> /MD) so the CRT
# agrees with every other cvcpkg bundle in the prefix.
$crt = if ($env:CVC_LINK -eq 'static') { '/MT' } else { '/MD' }

$srcs = @('imgui.cpp', 'imgui_draw.cpp', 'imgui_tables.cpp', 'imgui_widgets.cpp', 'imgui_demo.cpp')
$objs = @()
foreach ($s in $srcs) {
    $obj = [System.IO.Path]::ChangeExtension($s, '.obj')
    & cl /nologo /std:c++17 /O2 /EHsc $crt /I. /c $s "/Fo$obj"
    if ($LASTEXITCODE -ne 0) { throw "cl failed on $s" }
    $objs += $obj
}

& lib /nologo "/OUT:$(Join-Path $lib 'imgui.lib')" @objs
if ($LASTEXITCODE -ne 0) { throw 'lib.exe failed' }

# Public headers + the whole backends/ tree as source.
Copy-Item -Force imgui.h, imconfig.h, imgui_internal.h, `
    imstb_rectpack.h, imstb_textedit.h, imstb_truetype.h $incRoot
Copy-Item -Recurse -Force 'backends\*' $incBackends

Invoke-CvcRewriteInstallPaths
