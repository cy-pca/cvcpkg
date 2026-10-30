#!/usr/bin/env bash
# recipes/blender/build.sh — build Blender from source against the cvcpkg prefix (linux).
#
# Compiler: Blender 5.2's CMake refuses GCC < 14 and accepts Clang >= 17. The
# builders' GCC is 11, so this uses clang from the llvm build dependency, told
# (--gcc-install-dir) to use the same GCC installation `gcc` resolves to: its
# libstdc++ headers and runtime are the ones every C++ dependency in the prefix
# was built against, so the C++ ABI and the GLIBCXX symbol floor do not move.
#
# Libraries: WITH_LIBS_PRECOMPILED=OFF is Blender's "system libraries" mode
# (find_package against CMAKE_PREFIX_PATH), and WITH_STRICT_BUILD_OPTIONS=ON
# makes a feature whose library is not found a configure error instead of a
# silent downgrade, so the ON list below is what the bundle really has.
#
# Python: the prefix's python313 (shared libpython), WITH_PYTHON_INSTALL=OFF.
# bin/blender points Blender at the prefix (BLENDER_SYSTEM_PYTHON, which Blender
# hands to Python as its home), so Blender's Python and the prefix's python3.13
# share one site-packages.
#
# Layout: Blender's portable install (the executable beside its 5.2/ resource
# directory, which it finds relative to itself) under lib/blender/, so the tree
# relocates as a unit; bin/blender is a small launcher.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

BLENDER_VERSION_DIR="5.2"
APP_DIR="${CVC_INSTALL_DIR}/lib/blender"

# ── compiler ─────────────────────────────────────────────────────────────
CLANG_BIN="${CVC_BUILD_PREFIX:-${CVC_DEPS_PREFIX}}/bin"
for t in clang clang++ ld.lld; do
    [[ -x "${CLANG_BIN}/${t}" ]] || { echo "blender: ${CLANG_BIN}/${t} not found (llvm build dependency)" >&2; exit 1; }
done
GCC_INSTALL_DIR="$(dirname "$(gcc -print-libgcc-file-name)")"
[[ -d "${GCC_INSTALL_DIR}" ]] || { echo "blender: cannot locate the GCC installation" >&2; exit 1; }
export CC="${CLANG_BIN}/clang"
export CXX="${CLANG_BIN}/clang++"
export CFLAGS="--gcc-install-dir=${GCC_INSTALL_DIR} ${CFLAGS:-}"
export CXXFLAGS="--gcc-install-dir=${GCC_INSTALL_DIR} ${CXXFLAGS:-}"
export LDFLAGS="--gcc-install-dir=${GCC_INSTALL_DIR} ${LDFLAGS:-}"
"${CXX}" ${CXXFLAGS} -v -E -x c++ /dev/null -o /dev/null 2>&1 | grep -E 'clang version|Selected GCC installation' || true

# ── dependency search paths ──────────────────────────────────────────────
_pc=""
for _root in "${CVC_DEPS_PREFIX:-}" "${CVC_BUILD_PREFIX:-}"; do
    [[ -n "${_root}" ]] || continue
    _pc="${_pc:+${_pc}:}${_root}/lib/pkgconfig:${_root}/share/pkgconfig"
done
export PKG_CONFIG_PATH="${_pc}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

PY_VER="3.13"
[[ -f "${CVC_DEPS_PREFIX}/include/python${PY_VER}/Python.h" ]] \
    || { echo "blender: python${PY_VER} headers not found in ${CVC_DEPS_PREFIX}" >&2; exit 1; }

# ── configure ────────────────────────────────────────────────────────────
cmake -G Ninja \
    -S "${CVC_SOURCE_DIR}" \
    -B "${CVC_BUILD_DIR}" \
    -DCMAKE_BUILD_TYPE="${CMAKE_BUILD_TYPE}" \
    -DCMAKE_INSTALL_PREFIX="${APP_DIR}" \
    -DCMAKE_PREFIX_PATH="${CMAKE_PREFIX_PATH}" \
    -DCMAKE_C_COMPILER="${CC}" \
    -DCMAKE_CXX_COMPILER="${CXX}" \
    -DCMAKE_INSTALL_RPATH="\$ORIGIN/.." \
    -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DWITH_LIBS_PRECOMPILED=OFF \
    -DWITH_CPU_CHECK=OFF \
    -DWITH_STRICT_BUILD_OPTIONS=ON \
    -DWITH_INSTALL_PORTABLE=ON \
    -DWITH_BUILDINFO=OFF \
    -DWITH_GTESTS=OFF \
    -DWITH_DOC_MANPAGE=OFF \
    -DWITH_INSTALL_COPYRIGHT=OFF \
    -DWITH_COMPILER_CCACHE=OFF \
    -DWITH_LINKER_LLD=ON \
    -DWITH_LINKER_MOLD=OFF \
    -DWITH_BLENDER_THUMBNAILER=ON \
    \
    -DWITH_PYTHON=ON \
    -DWITH_PYTHON_INSTALL=OFF \
    -DWITH_PYTHON_INSTALL_NUMPY=OFF \
    -DWITH_PYTHON_INSTALL_REQUESTS=OFF \
    -DWITH_PYTHON_INSTALL_ZSTANDARD=OFF \
    -DWITH_PYTHON_NUMPY=ON \
    -DPYTHON_VERSION="${PY_VER}" \
    -DPYTHON_ROOT_DIR="${CVC_DEPS_PREFIX}" \
    \
    -DWITH_GHOST_X11=ON \
    -DWITH_GHOST_WAYLAND=OFF \
    -DWITH_GHOST_SDL=OFF \
    -DWITH_HEADLESS=OFF \
    -DWITH_X11_XINPUT=ON \
    -DWITH_X11_XFIXES=ON \
    -DWITH_OPENGL_BACKEND=ON \
    -DWITH_VULKAN_BACKEND=OFF \
    -DWITH_INPUT_NDOF=OFF \
    -DWITH_XR_OPENXR=OFF \
    -DWITH_RENDERDOC=OFF \
    -DWITH_TRACY=OFF \
    \
    -DWITH_TBB=ON \
    -DWITH_CYCLES=ON \
    -DWITH_CYCLES_EMBREE=OFF \
    -DWITH_CYCLES_OSL=OFF \
    -DWITH_CYCLES_PATH_GUIDING=OFF \
    -DWITH_CYCLES_DEVICE_CUDA=OFF \
    -DWITH_CYCLES_DEVICE_OPTIX=OFF \
    -DWITH_CYCLES_DEVICE_HIP=OFF \
    -DWITH_CYCLES_DEVICE_HIPRT=OFF \
    -DWITH_CYCLES_DEVICE_ONEAPI=OFF \
    -DWITH_CYCLES_CUDA_BINARIES=OFF \
    -DWITH_CYCLES_HIP_BINARIES=OFF \
    -DWITH_OPENIMAGEDENOISE=OFF \
    \
    -DWITH_OPENVDB=OFF \
    -DWITH_NANOVDB=OFF \
    -DWITH_OPENSUBDIV=OFF \
    -DWITH_ALEMBIC=OFF \
    -DWITH_USD=OFF \
    -DWITH_HYDRA=OFF \
    -DWITH_MATERIALX=OFF \
    -DWITH_MANIFOLD=OFF \
    -DWITH_POTRACE=OFF \
    -DWITH_HARU=OFF \
    -DWITH_LIBMV=OFF \
    -DWITH_DRACO=OFF \
    -DWITH_MESHOPTIMIZER=OFF \
    -DWITH_RUBBERBAND=OFF \
    \
    -DWITH_GMP=ON \
    -DWITH_FFTW3=ON \
    -DWITH_PUGIXML=ON \
    -DWITH_HARFBUZZ=ON \
    -DWITH_FRIBIDI=ON \
    -DWITH_IMAGE_OPENJPEG=ON \
    -DWITH_IMAGE_WEBP=ON \
    -DWITH_CODEC_FFMPEG=ON \
    -DWITH_CODEC_SNDFILE=ON \
    -DWITH_OPENAL=ON \
    -DWITH_SDL_AUDIO=OFF \
    -DWITH_JACK=ON \
    -DWITH_JACK_DYNLOAD=ON \
    -DWITH_PULSEAUDIO=ON \
    -DWITH_PULSEAUDIO_DYNLOAD=ON \
    -DWITH_PIPEWIRE=ON \
    -DWITH_PIPEWIRE_DYNLOAD=ON

cmake --build "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
cmake --install "${CVC_BUILD_DIR}"

[[ -x "${APP_DIR}/blender" && -d "${APP_DIR}/${BLENDER_VERSION_DIR}/scripts" ]] \
    || { echo "blender: install did not produce ${APP_DIR}/blender and ${BLENDER_VERSION_DIR}/" >&2; exit 1; }

# ── bin/blender launcher ─────────────────────────────────────────────────
# The prefix is the launcher's ../ (it follows a symlinked bin/blender back
# to the real file first).
#  - BLENDER_SYSTEM_PYTHON names the Python home Blender initialises.
#  - Add-on packages install legacy add-ons under share/blender/scripts/addons/
#    and extensions under share/blender/extensions/system/ (Blender's "System"
#    repository, module bl_ext.system.<id>). BLENDER_SYSTEM_SCRIPTS is a list
#    Blender adds to its own script paths, so the prefix's entry is prepended;
#    BLENDER_SYSTEM_EXTENSIONS is a single directory, so a caller's value wins.
mkdir -p "${CVC_INSTALL_DIR}/bin"
cat > "${CVC_INSTALL_DIR}/bin/blender" <<'LAUNCHER'
#!/bin/sh
# cvcpkg launcher for Blender: run lib/blender/blender with the prefix's
# Python and the add-ons installed in the prefix.
self="$0"
while [ -h "$self" ]; do
    link="$(readlink "$self")"
    case "$link" in
        /*) self="$link" ;;
        *)  self="$(dirname "$self")/$link" ;;
    esac
done
prefix="$(cd "$(dirname "$self")/.." && pwd -P)"
: "${BLENDER_SYSTEM_PYTHON:=$prefix}"
export BLENDER_SYSTEM_PYTHON
if [ -d "$prefix/share/blender/scripts" ]; then
    BLENDER_SYSTEM_SCRIPTS="$prefix/share/blender/scripts${BLENDER_SYSTEM_SCRIPTS:+:$BLENDER_SYSTEM_SCRIPTS}"
    export BLENDER_SYSTEM_SCRIPTS
fi
if [ -z "${BLENDER_SYSTEM_EXTENSIONS:-}" ] && [ -d "$prefix/share/blender/extensions" ]; then
    BLENDER_SYSTEM_EXTENSIONS="$prefix/share/blender/extensions"
    export BLENDER_SYSTEM_EXTENSIONS
fi
exec "$prefix/lib/blender/blender" "$@"
LAUNCHER
chmod 0755 "${CVC_INSTALL_DIR}/bin/blender"

# ── licences ─────────────────────────────────────────────────────────────
DOC_DIR="${CVC_INSTALL_DIR}/share/doc/blender"
mkdir -p "${DOC_DIR}"
cp "${CVC_SOURCE_DIR}/COPYING" "${DOC_DIR}/"
cp -R "${CVC_SOURCE_DIR}/doc/license" "${DOC_DIR}/license"

cvc_rewrite_install_paths
