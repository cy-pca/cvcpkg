#!/usr/bin/env bash
# recipes/ghostty/build.sh — build Ghostty from its release source tarball on
# Linux with the cvcpkg zig toolchain. macOS uses build-macos.sh.
#
#  1. Fetch: every Zig package build.zig.zon.json lists is `zig fetch`ed into
#     a package cache in the build dir, and zig must report exactly the
#     content hash pinned there (the JSON's key) -- a different archive at the
#     same URL hashes differently and fails here.
#  2. Build: `zig build --system <cache>/p`, which never touches the network,
#     with GTK 4 / libadwaita / freetype / fontconfig from the cvcpkg prefix and the libraries cvcpkg lacks (or has too old: harfbuzz)
#     compiled from the pinned packages.
#  3. Fix up the install tree: $ORIGIN RUNPATHs, no builder paths in the
#     desktop files, licences.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

GHOSTTY_VER="1.3.1"
# The release source tarball (minisign-verified when pinned, see recipe.yaml).
# Fetched here rather than by cvcpkg core: the CDN refuses core's Python-urllib
# User-Agent. Keep in step with recipe.yaml's source.artifacts.
SRC_URL="https://release.files.ghostty.org/${GHOSTTY_VER}/ghostty-${GHOSTTY_VER}.tar.gz"
SRC_SHA256="3349d25600ffbda281197a18314f7d18791969cffe9474f0ff16a45a9ebfccdb"

# The build prefix first: zig, pkg-config, tic, msgfmt and patchelf come from
# there.
export PATH="${CVC_BUILD_PREFIX:+${CVC_BUILD_PREFIX}/bin:}${CVC_DEPS_PREFIX}/bin:${PATH}"
_pc_dirs=()
for _p in "${CVC_DEPS_PREFIX}" ${CVC_BUILD_PREFIX:+"${CVC_BUILD_PREFIX}"}; do
    _pc_dirs+=("${_p}/lib/pkgconfig" "${_p}/share/pkgconfig")
done
PKG_CONFIG_PATH="$(IFS=:; echo "${_pc_dirs[*]}")"
export PKG_CONFIG_PATH
export LD_LIBRARY_PATH="${CVC_DEPS_PREFIX}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

for tool in zig pkg-config tic msgfmt patchelf; do
    command -v "${tool}" >/dev/null || { echo "ghostty: ${tool} not on PATH" >&2; exit 1; }
done
zig_ver="$(zig version)"
[ "${zig_ver}" = "0.15.2" ] || { echo "ghostty ${GHOSTTY_VER} needs zig 0.15.2, found ${zig_ver}" >&2; exit 1; }

ARCHIVE="${CVC_BUILD_DIR}/ghostty-${GHOSTTY_VER}.tar.gz"
echo "Downloading ${SRC_URL}..."
curl -fSL --retry 3 -o "${ARCHIVE}" "${SRC_URL}"
ACTUAL="$(sha256sum < "${ARCHIVE}" | awk '{print $1}')"
if [ "${ACTUAL}" != "${SRC_SHA256}" ]; then
    echo "sha256 mismatch for ghostty-${GHOSTTY_VER}.tar.gz: expected ${SRC_SHA256}, got ${ACTUAL}" >&2
    exit 1
fi
echo "sha256 OK (${ACTUAL})"
SRC="${CVC_BUILD_DIR}/ghostty-${GHOSTTY_VER}"
rm -rf "${SRC}" && mkdir -p "${SRC}"
tar xzf "${ARCHIVE}" -C "${SRC}" --strip-components=1
rm -f "${ARCHIVE}"
[ "$(cat "${SRC}/VERSION")" = "${GHOSTTY_VER}" ] \
    || { echo "ghostty: source tree is $(cat "${SRC}/VERSION"), want ${GHOSTTY_VER}" >&2; exit 1; }

# Keep zig's caches out of the builder account's ~/.cache/zig.
export ZIG_GLOBAL_CACHE_DIR="${CVC_BUILD_DIR}/zig-global-cache"
export ZIG_LOCAL_CACHE_DIR="${CVC_BUILD_DIR}/zig-local-cache"
PKG_CACHE="${ZIG_GLOBAL_CACHE_DIR}/p"

# ── 1. fetch and verify the pinned Zig packages ─────────────────────────
# build.zig.zon.json maps each package's Zig hash to its name and URL:
#   "<zig hash>": { "name": "...", "url": "...", "hash": "<nix sha256>" },
# with that indentation (it is generated). Only the Zig hash matters here.
DEPS="${CVC_BUILD_DIR}/zig-deps.txt"
awk -F'"' '
    /^  "[^"]+": \{$/ { h = $2 }
    /^    "name": /   { n = $4 }
    /^    "url": /    { u = $4 }
    /^  \},?$/        { print h, u, n }
' "${SRC}/build.zig.zon.json" > "${DEPS}"
want="$(grep -c '"url":' "${SRC}/build.zig.zon.json")"
[ "$(wc -l < "${DEPS}")" -eq "${want}" ] && [ "${want}" -gt 0 ] \
    || { echo "ghostty: parsed $(wc -l < "${DEPS}") of ${want} packages from build.zig.zon.json" >&2; exit 1; }

n=0
while read -r hash url name; do
    got=""
    for attempt in 1 2 3; do
        if got="$(zig fetch "${url}" 2>"${CVC_BUILD_DIR}/zig-fetch.err")"; then break; fi
        echo "zig fetch ${name} failed (attempt ${attempt}): $(cat "${CVC_BUILD_DIR}/zig-fetch.err")" >&2
        got=""
        sleep $((attempt * 5))
    done
    [ -n "${got}" ] || { echo "ghostty: could not fetch ${name} from ${url}" >&2; exit 1; }
    if [ "${got}" != "${hash}" ]; then
        echo "ghostty: ${name} from ${url} hashes to ${got}, pinned ${hash}" >&2
        exit 1
    fi
    n=$((n + 1))
done < "${DEPS}"
echo "fetched ${n} Zig packages, every hash as pinned in build.zig.zon.json"

# ── 2. build ────────────────────────────────────────────────────────────
# --system: packages come only from PKG_CACHE, and system integrations
# default to on (dynamic linking). -fsys / -fno-sys then choose per library:
# what cvcpkg packages comes from the prefix, the rest from the cache.
# -Dcpu=baseline: no host-specific instructions in a bundle that other
# machines run.
ZIG_ARGS=(
    --prefix "${CVC_INSTALL_DIR}"
    --system "${PKG_CACHE}"
    -Doptimize=ReleaseFast
    -Dcpu=baseline
    # Without a git checkout Ghostty versions itself 1.3.1-dev+0000000, on
    # the "tip" channel; a release build states its version.
    -Dversion-string="${GHOSTTY_VER}"
    -Dpie=true
    -Dapp-runtime=gtk
    -Dgtk-x11=true
    -Dgtk-wayland=true
    -Di18n=true
    # --system turns docs on, and they are rendered with pandoc, which cvcpkg
    # does not package: no man pages / HTML docs on linux (recipe notes).
    -Demit-docs=false
    -fsys=freetype
    -fsys=fontconfig
    # Ghostty 1.3 uses HarfBuzz 11 API (HB_BUFFER_CLUSTER_LEVEL_GRAPHEMES);
    # cvcpkg's harfbuzz is 10.2, so the pinned 11.0.0 is compiled in. GTK's
    # pango keeps the prefix's shared one (recipe notes).
    -fno-sys=harfbuzz
    -fno-sys=oniguruma
    -fno-sys=glslang
    -fno-sys=spirv-cross
    -fno-sys=simdutf
    -fno-sys=highway
    -fno-sys=gtk4-layer-shell
    -j"${CVC_JOBS}"
)
( cd "${SRC}" && zig build "${ZIG_ARGS[@]}" )

BIN="${CVC_INSTALL_DIR}/bin/ghostty"
[ -x "${BIN}" ] || { echo "ghostty: zig build installed no bin/ghostty" >&2; exit 1; }
[ -f "${CVC_INSTALL_DIR}/share/terminfo/x/xterm-ghostty" ] \
    || { echo "ghostty: no share/terminfo/x/xterm-ghostty (tic)" >&2; exit 1; }

# ── 3. fix up the install tree ──────────────────────────────────────────
# Libraries from the prefix and gtk4-layer-shell (installed into lib/)
# resolve through $ORIGIN; zig wrote the builder's -L directories instead.
patchelf --set-rpath '$ORIGIN/../lib' "${BIN}"
for so in "${CVC_INSTALL_DIR}"/lib/libgtk4-layer-shell.so*; do
    [ -f "${so}" ] && [ ! -L "${so}" ] && patchelf --set-rpath '$ORIGIN' "${so}"
done

# The install templates its own absolute path (the builder's install dir)
# into these; on PATH is the only location that stays true once installed.
sed -i "s|${CVC_INSTALL_DIR}/bin/ghostty|ghostty|g" \
    "${CVC_INSTALL_DIR}/share/applications/com.mitchellh.ghostty.desktop" \
    "${CVC_INSTALL_DIR}/share/metainfo/com.mitchellh.ghostty.metainfo.xml"
# D-Bus activation and the systemd user unit need an absolute path; they are
# session glue for a system install (see the recipe notes).
rm -rf "${CVC_INSTALL_DIR}/share/dbus-1" "${CVC_INSTALL_DIR}/share/systemd" \
       "${CVC_INSTALL_DIR}/lib/systemd"
# This package is the terminal. The install also lays down libghostty-vt
# (Ghostty's new terminal-core library, 0.1.0, with headers and a .pc) and
# gtk4-layer-shell's headers; nothing here needs them, so they are not shipped.
rm -rf "${CVC_INSTALL_DIR}"/lib/libghostty-vt.so* "${CVC_INSTALL_DIR}/include" \
       "${CVC_INSTALL_DIR}/share/pkgconfig"
if leftover="$(grep -rIl -- "${CVC_INSTALL_DIR}" "${CVC_INSTALL_DIR}" 2>/dev/null)"; then
    echo "ghostty: files still name the build's install dir:" >&2
    echo "${leftover}" >&2
    exit 1
fi

DOC="${CVC_INSTALL_DIR}/share/doc/ghostty"
mkdir -p "${DOC}/third-party"
cp -p "${SRC}/LICENSE" "${DOC}/LICENSE"
# Every fetched package's own licence files, by package name.
while read -r hash url name; do
    found=0
    for f in "${PKG_CACHE}/${hash}"/LICEN[CS]E* "${PKG_CACHE}/${hash}"/COPYING* \
             "${PKG_CACHE}/${hash}"/OFL* "${PKG_CACHE}/${hash}"/*/OFL.txt; do
        [ -f "${f}" ] || continue
        mkdir -p "${DOC}/third-party/${name}"
        cp -p "${f}" "${DOC}/third-party/${name}/"
        found=1
    done
    [ "${found}" = 1 ] || echo "note: no licence file at the top of package ${name} (${url##*/})"
done < "${DEPS}"

"${BIN}" --version
