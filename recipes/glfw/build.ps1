# recipes/glfw/build.ps1 — build GLFW on Windows via CMake + MSVC.
#
# On Windows GLFW uses the Win32/WGL backend (no X11/Wayland).  CVC_LINK is
# translated into GLFW_LIBRARY_TYPE (STATIC/SHARED); Invoke-CvcCMakeBuild also
# sets BUILD_SHARED_LIBS and the MSVC runtime from CVC_LINK.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$glfwLibType = if ($env:CVC_LINK -eq 'static') { 'STATIC' } else { 'SHARED' }

Invoke-CvcCMakeBuild @(
    "-DGLFW_LIBRARY_TYPE=$glfwLibType",
    '-DGLFW_BUILD_EXAMPLES=OFF',
    '-DGLFW_BUILD_TESTS=OFF',
    '-DGLFW_BUILD_DOCS=OFF'
)
