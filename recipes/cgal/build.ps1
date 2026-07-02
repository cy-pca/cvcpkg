# recipes/cgal/build.ps1 — build CGAL on Windows with MSVC.
#
# CGAL is mostly header-only; the non-header parts (Core, ImageIO, Qt)
# are opt-in.  We build just Core (needed by exact-arithmetic kernels)
# and rely on GMP + MPFR + Boost being staged into $CVC_DEPS_PREFIX
# by their own recipes.
$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DCGAL_HEADER_ONLY=OFF',
    '-DWITH_CGAL_Core=ON',
    '-DWITH_CGAL_ImageIO=OFF',
    '-DWITH_CGAL_Qt6=OFF',
    '-DBUILD_TESTING=OFF',
    '-DWITH_examples=OFF',
    '-DWITH_demos=OFF'
)
