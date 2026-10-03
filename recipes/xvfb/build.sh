#!/usr/bin/env bash
# recipes/xvfb/build.sh — build Xvfb (X.Org xserver 21.1.24) with Meson, GLX on,
# everything else off, relocatable; install the xvfb-run helper.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

# NUL-out stale absolute build-prefix strings left inside an ELF.  Meson keeps
# external RPATH entries and patchelf (which rewrites RUNPATH) leaves the old
# text orphaned in .dynstr; nothing references it, but it would leak the
# builder's temporary prefix into the bundle.  Same-length overwrite, so no
# offsets move.
_scrub_stale_prefix_strings() {
    local f="$1" root off str
    for root in "${CVC_DEPS_PREFIX:-}" "${CVC_BUILD_PREFIX:-}" "${CVC_INSTALL_DIR}" \
                "${CVC_SOURCE_DIR}" "${CVC_BUILD_DIR}"; do
        [[ -n "${root}" ]] || continue
        while IFS=: read -r off str; do
            [[ -n "${off}" ]] || continue
            head -c "${#str}" /dev/zero | dd of="${f}" bs=1 seek="${off}" conv=notrunc status=none
        done < <(LC_ALL=C grep -aboP "\\Q${root}\\E[^\\x00]*" "${f}" || true)
    done
}

# ── dri.pc shim ─────────────────────────────────────────────────────────────
# The server's GLX module hard-requires pkg-config module `dri` (Mesa's
# dri.pc), but only for GL/internal/dri_interface.h and the default driver
# directory.  Mesa is a RUNTIME companion (cvcpkg `mesa`), not a build
# dependency — building xvfb must not drag LLVM in — so ship the one header
# (verbatim from Mesa 25.3.6, MIT; the interface is versioned per-extension and
# backward compatible) in this recipe and describe it with a throw-away dri.pc.
# The dridriverdir below is only the last-resort fallback baked into Xvfb;
# relocatable-paths.patch makes it look in <prefix>/lib/dri first.
_shim="${CVC_BUILD_DIR}-dri-shim"
mkdir -p "${_shim}/lib/pkgconfig"
cat > "${_shim}/lib/pkgconfig/dri.pc" <<DRIPC
dridriverdir=/usr/lib/dri

Name: dri
Description: Direct Rendering Infrastructure (cvcpkg xvfb build shim)
Version: 25.3.6
Cflags: -I${SCRIPT_DIR}/dri-shim
DRIPC

_pcpath="${_shim}/lib/pkgconfig:${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/libdata/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig"
if [[ -n "${CVC_BUILD_PREFIX:-}" ]]; then
    _pcpath="${_pcpath}:${CVC_BUILD_PREFIX}/lib/pkgconfig:${CVC_BUILD_PREFIX}/libdata/pkgconfig:${CVC_BUILD_PREFIX}/share/pkgconfig"
fi
export PKG_CONFIG_PATH="${_pcpath}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

_default_lib=shared
[[ "${CVC_LINK:-shared}" == "static" ]] && _default_lib=static

# Defaults that would otherwise bake this build's temporary install prefix into
# the binary are pinned to conventional system paths; the patched server prefers
# the files that live next to it (bin/xkbcomp, share/X11/xkb, lib/dri):
#   xkb_dir/xkb_bin_dir  fallback only      xkb_output_dir  /tmp (always writable)
#   default_font_path    built-ins           = libXfont2's compiled-in fixed/cursor
#                                              fonts, so no font files are needed
cd "${CVC_SOURCE_DIR}"
meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --default-library="${_default_lib}" \
    --pkg-config-path="${_pcpath//:/,}" \
    -Dxvfb=true \
    -Dxorg=false \
    -Dxephyr=false \
    -Dxnest=false \
    -Dxwin=false \
    -Dxquartz=false \
    -Dglx=true \
    -Dglamor=false \
    -Ddri1=false \
    -Ddri2=false \
    -Ddri3=false \
    -Ddrm=false \
    -Dudev=false \
    -Dudev_kms=false \
    -Dhal=false \
    -Dsystemd_logind=false \
    -Dpciaccess=false \
    -Dint10=false \
    -Dvgahw=false \
    -Ddga=false \
    -Dsecure-rpc=false \
    -Dxdmcp=false \
    -Dxdm-auth-1=false \
    -Dxcsecurity=false \
    -Dxselinux=false \
    -Dxf86bigfont=false \
    -Dxf86-input-inputtest=false \
    -Dsuid_wrapper=false \
    -Dsha1=libnettle \
    -Ddocs=false \
    -Ddevel-docs=false \
    -Ddocs-pdf=false \
    -Dxkb_dir=/usr/share/X11/xkb \
    -Dxkb_bin_dir=/usr/bin \
    -Dxkb_output_dir=/tmp \
    -Ddefault_font_path=built-ins \
    -Dc_link_args="-Wl,-rpath,\$ORIGIN/../lib"
ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

# ── xvfb-run helper ─────────────────────────────────────────────────────────
install -m 0755 "${SCRIPT_DIR}/xvfb-run" "${CVC_INSTALL_DIR}/bin/xvfb-run"

# ── Relocation ──────────────────────────────────────────────────────────────
# RUNPATH $ORIGIN/../lib so the cvcpkg libs (libXfont2, libxkbfile, pixman,
# nettle, ...) resolve from whatever prefix the bundle lands in.
if ! command -v patchelf >/dev/null 2>&1; then
    echo "cvcpkg: patchelf not found — Xvfb RUNPATH would not be relocatable" >&2
    exit 1
fi
patchelf --set-rpath '$ORIGIN/../lib' "${CVC_INSTALL_DIR}/bin/Xvfb"
_scrub_stale_prefix_strings "${CVC_INSTALL_DIR}/bin/Xvfb"

find "${CVC_INSTALL_DIR}" -name '*.la' -delete

cvc_rewrite_install_paths
