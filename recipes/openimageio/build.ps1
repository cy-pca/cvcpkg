# recipes/openimageio/build.ps1 — build OpenImageIO and its tools on Windows via CMake + MSVC.
# Same dependency policy and feature set as build.sh (see the comment there).
$ErrorActionPreference = 'Stop'

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
. "$scriptDir\..\_common\env-windows.ps1"

$required = 'ZLIB;Imath;OpenEXR;libjpeg-turbo;TIFF;PNG;OpenColorIO;TBB;OpenJPEG;openjph;WebP;Robinmap;fmt;pugixml'

Invoke-CvcCMakeBuild @(
    "-DOpenImageIO_REQUIRED_DEPS=$required",
    '-DOpenImageIO_BUILD_MISSING_DEPS=',
    "-DZLIB_ROOT=$env:CVC_DEPS_PREFIX",
    '-DOIIO_BUILD_TOOLS=ON',
    '-DOIIO_BUILD_TESTS=OFF',
    '-DBUILD_TESTING=OFF',
    '-DSTOP_ON_WARNING=OFF',
    '-DLINKSTATIC=OFF',
    '-DINSTALL_DOCS=OFF',
    '-DINSTALL_FONTS=OFF',
    '-DUSE_PYTHON=OFF',
    '-DUSE_QT=OFF',
    '-DUSE_FREETYPE=OFF',
    '-DUSE_FFMPEG=OFF',
    '-DUSE_OPENCV=OFF',
    '-DUSE_OPENVDB=OFF',
    '-DUSE_PTEX=OFF',
    '-DUSE_LIBRAW=OFF',
    '-DUSE_LIBHEIF=OFF',
    '-DUSE_JXL=OFF',
    '-DUSE_GIF=OFF',
    '-DUSE_DCMTK=OFF',
    '-DUSE_NUKE=OFF',
    '-DUSE_R3DSDK=OFF',
    '-DUSE_LIBUHDR=OFF',
    '-DUSE_TBB=ON',
    '-DUSE_OPENCOLORIO=ON',
    '-DUSE_OPENJPEG=ON',
    '-DUSE_WEBP=ON',
    '-DUSE_EXTERNAL_PUGIXML=ON',
    '-DUSE_SIMD=sse4.2'
)
