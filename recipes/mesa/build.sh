#!/usr/bin/env bash
# recipes/mesa/build.sh — Mesa 25.3 software GL (llvmpipe + softpipe) with
# GLX (X11 platform, via GLVND) and EGL, relocatable ($ORIGIN RUNPATHs).
#
# Mesa >= 24.2 dropped the LIBGL_DRIVERS_PATH / per-driver dlopen scheme for
# GLX: libGLX_mesa.so links libgallium-<ver>.so directly, so the only things
# that must be found at run time are plain DT_NEEDED libraries, which an
# $ORIGIN RUNPATH resolves from any prefix.  Only lib/dri/*_dri.so (used by X
# servers that load swrast_dri.so themselves) still needs a driver dir.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/libdata/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib/pkgconfig:${CVC_BUILD_PREFIX}/libdata/pkgconfig:${CVC_BUILD_PREFIX}/share/pkgconfig}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

# Locate a tool in the dependency prefixes (runtime closure first, then the
# build-only closure); fall back to PATH.
_find_tool() {
    local name="$1" root
    for root in "${CVC_DEPS_PREFIX}" "${CVC_BUILD_PREFIX:-}"; do
        [[ -n "${root}" && -x "${root}/bin/${name}" ]] && { echo "${root}/bin/${name}"; return 0; }
    done
    command -v "${name}" || true
}

# cvcpkg's bison/flex/m4 were built into a scratch install dir whose absolute
# paths are baked into the binaries (pkgdatadir, m4 location) and no longer
# exist; point them at the copies in the dependency prefix.  (Never fall back
# to the host /usr/bin/m4 or /usr/share/bison.)
for _root in "${CVC_DEPS_PREFIX}" "${CVC_BUILD_PREFIX:-}"; do
    [[ -n "${_root}" ]] || continue
    if [[ -d "${_root}/share/bison" && -z "${BISON_PKGDATADIR:-}" ]]; then
        export BISON_PKGDATADIR="${_root}/share/bison"
    fi
    if [[ -x "${_root}/bin/m4" && -z "${M4:-}" ]]; then
        export M4="${_root}/bin/m4"
    fi
done
unset _root

_python="$(_find_tool python3.11)"
_llvm_config="$(_find_tool llvm-config)"
_patchelf="$(_find_tool patchelf)"
: "${_python:?cvcpkg: python3.11 (cvcpkg python311) not found}"
: "${_llvm_config:?cvcpkg: llvm-config (cvcpkg llvm20) not found}"
: "${_patchelf:?cvcpkg: patchelf (cvcpkg patchelf) not found}"

# Mesa's meson.build does find_installation('python3'), which otherwise means
# "the interpreter running meson" (not ours, so no mako/yaml).  Pin it, and
# llvm-config, with a native file.
_native="${CVC_BUILD_DIR}.native.ini"
cat > "${_native}" <<NATIVE
[binaries]
python3 = '${_python}'
llvm-config = '${_llvm_config}'
NATIVE

_default_lib=shared
[[ "${CVC_LINK:-shared}" == "static" ]] && _default_lib=static

# gbm is enabled only because Mesa builds its lib/dri/swrast_dri.so megadriver
# (the file an X server's software GLX loads) under `with_gallium and with_gbm`.
# Tools to leave out are all disabled explicitly so a host-installed copy
# (valgrind, libunwind, lm-sensors, selinux, ...) can never leak into the
# closure — see cvcpkg hermetic-toolchain policy.
cd "${CVC_SOURCE_DIR}"
meson setup "${CVC_BUILD_DIR}" \
    --native-file "${_native}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --default-library="${_default_lib}" \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig,${CVC_DEPS_PREFIX}/libdata/pkgconfig,${CVC_DEPS_PREFIX}/share/pkgconfig${CVC_BUILD_PREFIX:+,${CVC_BUILD_PREFIX}/lib/pkgconfig,${CVC_BUILD_PREFIX}/share/pkgconfig}" \
    -Db_ndebug=true \
    -Dcpp_rtti=false \
    -Dplatforms=x11 \
    -Dgallium-drivers=llvmpipe,softpipe \
    -Dvulkan-drivers= \
    -Dvulkan-layers= \
    -Dglx=dri \
    -Dglvnd=enabled \
    -Degl=enabled \
    -Dgles1=disabled \
    -Dgles2=enabled \
    -Dgbm=enabled \
    -Dopengl=true \
    -Dllvm=enabled \
    -Dshared-llvm=enabled \
    -Dxlib-lease=disabled \
    -Dgallium-va=disabled \
    -Dgallium-rusticl=false \
    -Dvideo-codecs= \
    -Dvalgrind=disabled \
    -Dlibunwind=disabled \
    -Dlmsensors=disabled \
    -Dselinux=false \
    -Dtools= \
    -Dbuild-tests=false \
    -Dzstd=enabled \
    -Dexpat=enabled
ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

# ── Relocation ──────────────────────────────────────────────────────────────
# Meson strips the build-tree RUNPATH on install; stamp $ORIGIN-relative ones so
# libGLX_mesa / libEGL_mesa / libgallium find their siblings (libLLVM, libdrm,
# libxcb, libX11, ...) in whatever prefix the bundle lands in.  lib/dri/*.so
# and lib/gbm/*.so sit one level down.
shopt -s nullglob
for _so in "${CVC_INSTALL_DIR}"/lib/*.so*; do
    [[ -L "${_so}" ]] && continue
    "${_patchelf}" --set-rpath '$ORIGIN' "${_so}"
done
for _so in "${CVC_INSTALL_DIR}"/lib/dri/*.so* "${CVC_INSTALL_DIR}"/lib/gbm/*.so*; do
    [[ -L "${_so}" ]] && continue
    "${_patchelf}" --set-rpath '$ORIGIN:$ORIGIN/..' "${_so}"
done
shopt -u nullglob

# Never ship the libtool archives or anything pointing back at the build tree.
find "${CVC_INSTALL_DIR}" -name '*.la' -delete

cvc_rewrite_install_paths
