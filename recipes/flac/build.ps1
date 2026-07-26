# recipes/flac/build.ps1 — build FLAC (libFLAC + libFLAC++) on Windows via CMake + MSVC.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

# BUILD_SHARED_LIBS is set from CVC_LINK by Invoke-CvcCMakeBuild; FLAC honors it.
Invoke-CvcCMakeBuild @(
    "-DCMAKE_PREFIX_PATH=$env:CVC_DEPS_PREFIX",
    '-DWITH_OGG=ON',
    '-DBUILD_TESTING=OFF',
    '-DBUILD_EXAMPLES=OFF',
    '-DBUILD_DOCS=OFF',
    '-DBUILD_PROGRAMS=OFF',
    '-DINSTALL_MANPAGES=OFF'
)
