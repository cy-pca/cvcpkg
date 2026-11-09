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
#
# On macOS, FindOpenGL resolves OpenGL.framework inside the build host's Xcode
# SDK (/Applications/Xcode_<ver>.app/.../OpenGL.framework), and GLEW exports
# that absolute path in GLEW::glew's link interface, which exists on no other
# Mac. Name the framework instead.
_glew_extra=()
case "${CVC_PLATFORM}" in
    linux)
        _glew_extra+=(-DCMAKE_PREFIX_PATH="${CVC_DEPS_PREFIX}")
        ;;
    macos)
        _glew_extra+=(
            "-DOPENGL_gl_LIBRARY=-framework OpenGL"
            "-DOPENGL_glu_LIBRARY=-framework OpenGL"
        )
        ;;
esac

cvc_cmake_build \
    -DBUILD_UTILS=OFF \
    -DGLEW_REGAL=OFF \
    -DGLEW_OSMESA=OFF \
    ${_glew_extra[@]+"${_glew_extra[@]}"}

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
