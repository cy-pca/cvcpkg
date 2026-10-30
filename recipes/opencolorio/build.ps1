# recipes/opencolorio/build.ps1 — build the OpenColorIO library on Windows via CMake + MSVC.
# Same switches as build.sh. IMATH_DLL, which Blender's own build adds by hand,
# comes from the cvcpkg Imath's exported Imath::Imath target here.
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

Invoke-CvcCMakeBuild @(
    '-DOCIO_BUILD_APPS=OFF',
    '-DOCIO_BUILD_PYTHON=OFF',
    '-DOCIO_BUILD_NUKE=OFF',
    '-DOCIO_BUILD_JAVA=OFF',
    '-DOCIO_BUILD_OPENFX=OFF',
    '-DOCIO_BUILD_DOCS=OFF',
    '-DOCIO_BUILD_TESTS=OFF',
    '-DOCIO_BUILD_GPU_TESTS=OFF',
    '-DOCIO_USE_OIIO_FOR_APPS=OFF',
    '-DOCIO_INSTALL_EXT_PACKAGES=NONE',
    "-DZLIB_ROOT=$env:CVC_DEPS_PREFIX",
    '-DOCIO_USE_SIMD=ON'
)
