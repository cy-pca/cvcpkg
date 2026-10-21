# recipes/tvision/build.ps1 — build magiblot/tvision (Turbo Vision C++ TUI) on Windows via MSVC.
#
# On Windows tvision draws through the Win32 console API, so there is no ncurses. Upstream's
# install target is used as is: it installs the static tvision.lib, the public headers and a
# CMake package (lib/cmake/tvision/tvision-config.cmake, target tvision::tvision). On Windows that
# config names no external libraries, so it is relocatable. (build.sh stages by hand on Unix,
# where the exported config would carry the build host's absolute ncurses paths.)
#
# tvision is always a STATIC library (add_library(... STATIC)); BUILD_SHARED_LIBS does not apply.
# It links the dynamic CRT (TV_USE_STATIC_RTL stays OFF), matching the shared Windows bundles
# that link it, such as libcvc's cvc.dll.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DTV_BUILD_EXAMPLES=OFF',
    '-DTV_BUILD_TESTS=OFF',
    '-DTV_USE_STATIC_RTL=OFF'
)
