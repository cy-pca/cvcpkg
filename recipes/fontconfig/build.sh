#!/usr/bin/env bash
# recipes/fontconfig/build.sh — build Fontconfig with Meson.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

_default_lib=shared
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    _default_lib=static
fi

if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    _rpath_flags="-Wl,-rpath,@loader_path"
else
    _rpath_flags="-Wl,-rpath,\$ORIGIN"
fi

cd "${CVC_SOURCE_DIR}"

meson setup "${CVC_BUILD_DIR}" \
    --wrap-mode=nofallback \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --default-library="${_default_lib}" \
    --pkg-config-path="${CVC_DEPS_PREFIX}/lib/pkgconfig" \
    -Dc_link_args="${_rpath_flags}" \
    -Dtests=disabled \
    -Ddoc=disabled \
    -Dcache-build=disabled \
    -Dtools=enabled

ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

cvc_rewrite_install_paths

# ── relocatable configuration ─────────────────────────────────────────
# unix-relocatable-config.patch makes the library look for etc/fonts next to
# its own lib/ (or bin/) before the compiled-in path, which names this job's
# staging prefix. What it then reads must not name the staging prefix either:
#  * <cachedir> only understands prefix="xdg", so the system cache dir meson
#    wrote (<staging>/var/cache/fontconfig) cannot be made relative. Drop it;
#    the per-user XDG cache (~/.cache/fontconfig) and ~/.fontconfig remain.
#  * Add <dir prefix="relative">../../share/fonts</dir> beside the system
#    font dirs, so fonts installed into the same prefix (liberation-fonts,
#    carlito-fonts) are found wherever the prefix is unpacked.
#  * conf.d/README names conf.avail by its staging path in prose.
# awk + sed -i.bak only: this must run on GNU and BSD userlands alike.
FC_ETC="${CVC_INSTALL_DIR}/etc/fonts"
if [ -f "${FC_ETC}/fonts.conf" ]; then
    awk '
        /<cachedir>\/[^<]*<\/cachedir>/ { next }
        /<dir prefix="xdg">fonts<\/dir>/ && !done {
            match($0, /^[ \t]*/)
            printf "%s<dir prefix=\"relative\">../../share/fonts</dir>\n", substr($0, RSTART, RLENGTH)
            done = 1
        }
        { print }
        END { if (!done) exit 3 }
    ' "${FC_ETC}/fonts.conf" > "${FC_ETC}/fonts.conf.new" \
        || { echo "fonts.conf: no <dir prefix=\"xdg\">fonts</dir> anchor" >&2; exit 1; }
    mv "${FC_ETC}/fonts.conf.new" "${FC_ETC}/fonts.conf"
    if [ -f "${FC_ETC}/conf.d/README" ]; then
        sed -i.bak "s|${CVC_INSTALL_DIR}|<prefix>|g" "${FC_ETC}/conf.d/README"
        rm -f "${FC_ETC}/conf.d/README.bak"
    fi
    if grep -rIn "${CVC_INSTALL_DIR}" "${FC_ETC}" "${CVC_INSTALL_DIR}/share/fontconfig" 2>/dev/null; then
        echo "fontconfig config still names the staging prefix (above)" >&2
        exit 1
    fi
fi
