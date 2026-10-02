#!/usr/bin/env bash
# recipes/libxkbfile/build.sh — build libxkbfile 1.2.0 with Meson.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/libdata/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib/pkgconfig:${CVC_BUILD_PREFIX}/libdata/pkgconfig:${CVC_BUILD_PREFIX}/share/pkgconfig}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

_default_lib=shared
[[ "${CVC_LINK:-shared}" == "static" ]] && _default_lib=static

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

cd "${CVC_SOURCE_DIR}"
meson setup "${CVC_BUILD_DIR}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --default-library="${_default_lib}" \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig,${CVC_DEPS_PREFIX}/libdata/pkgconfig,${CVC_DEPS_PREFIX}/share/pkgconfig" \
    -Dc_link_args="-Wl,-rpath,\$ORIGIN"
ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

# libxkbfile only needs its sibling libX11 etc.: RUNPATH = $ORIGIN, nothing else.
for _so in "${CVC_INSTALL_DIR}"/lib/libxkbfile.so.*.*; do
    [[ -L "${_so}" ]] && continue
    patchelf --set-rpath '$ORIGIN' "${_so}"
    _scrub_stale_prefix_strings "${_so}"
done

cvc_rewrite_install_paths
