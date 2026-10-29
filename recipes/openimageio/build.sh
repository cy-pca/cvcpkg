#!/usr/bin/env bash
# recipes/openimageio/build.sh — build OpenImageIO and its command-line tools.
#
# OpenImageIO_REQUIRED_DEPS turns each listed optional dependency into a hard
# requirement, so a format silently dropping out (a find_package that missed
# the prefix) fails the configure instead of shipping a smaller OIIO.
# OpenImageIO_BUILD_MISSING_DEPS stays empty: OIIO never downloads a dependency.
# USE_SIMD=sse4.2 on x86_64 matches Blender's own build (its minimum CPU).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

required="ZLIB;Imath;OpenEXR;libjpeg-turbo;TIFF;PNG;OpenColorIO;TBB;OpenJPEG;openjph;WebP;Robinmap;fmt;pugixml"

simd=()
case "$(uname -m)" in
    x86_64|amd64) simd=(-DUSE_SIMD=sse4.2) ;;
esac

cvc_cmake_build \
    -DOpenImageIO_REQUIRED_DEPS="${required}" \
    -DOpenImageIO_BUILD_MISSING_DEPS="" \
    -DOIIO_BUILD_TOOLS=ON \
    -DOIIO_BUILD_TESTS=OFF \
    -DBUILD_TESTING=OFF \
    -DSTOP_ON_WARNING=OFF \
    -DLINKSTATIC=OFF \
    -DINSTALL_DOCS=OFF \
    -DINSTALL_FONTS=OFF \
    -DUSE_PYTHON=OFF \
    -DUSE_QT=OFF \
    -DUSE_FREETYPE=OFF \
    -DUSE_FFMPEG=OFF \
    -DUSE_OPENCV=OFF \
    -DUSE_OPENVDB=OFF \
    -DUSE_PTEX=OFF \
    -DUSE_LIBRAW=OFF \
    -DUSE_LIBHEIF=OFF \
    -DUSE_JXL=OFF \
    -DUSE_GIF=OFF \
    -DUSE_DCMTK=OFF \
    -DUSE_NUKE=OFF \
    -DUSE_R3DSDK=OFF \
    -DUSE_LIBUHDR=OFF \
    -DUSE_TBB=ON \
    -DUSE_OPENCOLORIO=ON \
    -DUSE_OPENJPEG=ON \
    -DUSE_WEBP=ON \
    -DUSE_EXTERNAL_PUGIXML=ON \
    ${simd[@]+"${simd[@]}"}
