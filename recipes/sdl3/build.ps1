# recipes/sdl3/build.ps1 — build SDL 3.x on Windows via CMake + MSVC.
#
# On Windows SDL uses its native Win32 / Direct3D / WASAPI backends, so
# no X11 or Wayland dependencies are required here.  Honour CVC_LINK via
# SDL's own SDL_STATIC / SDL_SHARED switch (exactly one artifact per
# bundle), matching the Unix build.sh.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

if ($env:CVC_LINK -eq 'static') {
    $sdlStatic = 'ON'
    $sdlShared = 'OFF'
} else {
    $sdlStatic = 'OFF'
    $sdlShared = 'ON'
}

Invoke-CvcCMakeBuild @(
    "-DSDL_STATIC=$sdlStatic",
    "-DSDL_SHARED=$sdlShared",
    '-DSDL_TEST_LIBRARY=OFF',
    '-DSDL_TESTS=OFF',
    '-DSDL_EXAMPLES=OFF',
    '-DSDL_INSTALL_TESTS=OFF'
)
