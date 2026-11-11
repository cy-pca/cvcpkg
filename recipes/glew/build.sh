#!/usr/bin/env bash
# recipes/glew/build.sh — build GLEW from source with CMake.
#
# GLEW's CMake project lives in build/cmake (not the repo root), so point
# CVC_SOURCE_DIR there before invoking the shared helper. On Linux it links
# cvcpkg's libglvnd (the GL dispatch; drivers stay system-provided); elsewhere
# the system OpenGL.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export CVC_SOURCE_DIR="${CVC_SOURCE_DIR}/build/cmake"

# On Linux, GL, GLU and Xlib (with libSM/libICE) come from cvcpkg's libglvnd,
# glu, libx11, libsm and libice (recipe.yaml), so FindOpenGL/FindX11 must
# search the deps prefix.
_glew_extra=()
if [[ "${CVC_PLATFORM}" == "linux" ]]; then
    _glew_extra+=(-DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}")
fi

cvc_cmake_build \
    -DBUILD_UTILS=OFF \
    -DGLEW_REGAL=OFF \
    -DGLEW_OSMESA=OFF \
    ${_glew_extra[@]+"${_glew_extra[@]}"}

# On macOS, FindOpenGL resolves OpenGL.framework inside the build host's Xcode
# SDK, and GLEW exports that absolute path in GLEW::glew's link interface
# (/Applications/Xcode_<ver>.app/.../MacOSX<ver>.sdk/System/Library/Frameworks/
# OpenGL.framework), which exists on no other Mac. Rewrite it to the system
# framework path every Mac has; CMake links a .framework path as
# "-framework OpenGL" and the linker finds it in the consumer's own SDK.
# (Configuring with OPENGL_gl_LIBRARY="-framework OpenGL" instead does not
# work: CMake takes that list item for a file and ninja fails on it.)
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    for _f in "${CVC_INSTALL_DIR}"/lib/cmake/glew/*.cmake; do
        sed -i.bak -E \
            's#[^";]*/SDKs/MacOSX[^/";]*\.sdk(/System/Library/Frameworks/[^/";]+\.framework)#\1#g' \
            "${_f}"
        rm -f "${_f}.bak"
    done
fi

# Refuse to package a GLEW::glew that still names a build-host path: no
# /usr/lib*, /usr/local, /opt or Xcode/SDK path may be left in the export
# after cvc_rewrite_install_paths. (OpenBSD's base X11 is /usr/X11R6 and
# Windows names opengl32/glu32, so only linux and macOS are checked.)
case "${CVC_PLATFORM}" in
    linux|macos)
        _glew_leaks="$(grep -rnE '/usr/lib|/usr/local|/opt/|/Applications/|\.sdk/' \
            "${CVC_INSTALL_DIR}/lib/cmake/glew" "${CVC_INSTALL_DIR}/lib/pkgconfig" || true)"
        if [[ -n "${_glew_leaks}" ]]; then
            echo "glew: the installed CMake/pkg-config files name build-host paths:" >&2
            echo "${_glew_leaks}" >&2
            exit 1
        fi
        ;;
esac
