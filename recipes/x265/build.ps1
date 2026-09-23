# recipes/x265/build.ps1 — build x265 H.265/HEVC encoder on Windows via CMake + MSVC.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Import-CvcMsvcEnv

$shared = if ($env:CVC_LINK -eq 'static') { 'OFF' } else { 'ON' }
$static = if ($env:CVC_LINK -eq 'static') { 'ON' } else { 'OFF' }

# Put nasm on PATH for assembly optimisations. It is a HOST TOOL, so it is
# staged into CVC_BUILD_PREFIX, not the runtime prefix — searching only
# CVC_DEPS_PREFIX found nothing and x265 quietly configured itself without asm
# rather than failing. Build prefix first, so the pinned nasm wins.
# Native PowerShell PATH here (backslashes, ';'), because cmake is invoked
# directly from PowerShell rather than through bash.
$toolRoots = @($env:CVC_BUILD_PREFIX, $env:CVC_DEPS_PREFIX) | Where-Object { $_ }
foreach ($r in $toolRoots) { $env:PATH = "$r\bin;$env:PATH" }

# x265's CMakeLists sets CMP0054 OLD and declares cmake_minimum_required < 3.5.
# CMake 4.x removed both, so configure dies with
#   Compatibility with CMake < 3.5 has been removed from CMake.
# Which cmake is picked depends on PATH ordering between cvcpkg's staged 3.31.7
# and any newer system install, so this failed intermittently. The shared
# Invoke-CvcCMakeBuild helper already passes this flag; recipes that call cmake
# directly have to pass it themselves.
& cmake -G Ninja `
    -S "$env:CVC_SOURCE_DIR\source" `
    -B $env:CVC_BUILD_DIR `
    "-DCMAKE_INSTALL_PREFIX=$env:CVC_INSTALL_DIR" `
    -DCMAKE_BUILD_TYPE=Release `
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 `
    -DCMAKE_POSITION_INDEPENDENT_CODE=ON `
    "-DBUILD_SHARED_LIBS=$shared" `
    "-DENABLE_SHARED=$shared" `
    "-DENABLE_STATIC=$static" `
    -DENABLE_CLI=OFF `
    -DENABLE_TESTS=OFF `
    -DLIB_INSTALL_DIR=lib
if ($LASTEXITCODE -ne 0) { throw 'x265 cmake configure failed' }

& cmake --build $env:CVC_BUILD_DIR -j $env:CVC_JOBS
if ($LASTEXITCODE -ne 0) { throw 'x265 cmake build failed' }

& cmake --install $env:CVC_BUILD_DIR
if ($LASTEXITCODE -ne 0) { throw 'x265 cmake install failed' }

Invoke-CvcRewriteInstallPaths
