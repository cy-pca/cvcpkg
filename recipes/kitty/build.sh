#!/usr/bin/env bash
# recipes/kitty/build.sh — download and stage the official kitty binaries on
# linux (x86_64, arm64) and macOS. kitty has no Windows build.
#
#   linux   kitty-<ver>-<arch>.txz: upstream's self-contained bundle (bin/,
#           lib/, share/) with its own Python 3.14 and most of its libraries.
#           It is staged whole in lib/kitty/; bin/* are launchers that point
#           xkbcommon and libX11 at the prefix's XKB and locale data first
#           (see the recipe notes). What the bundle does not carry -- libX11,
#           libXcursor, libxcb, fontconfig and the X11 extensions GLFW
#           dlopens -- are this recipe's runtime dependencies, and cvcpkg's
#           relocation pass gives the objects that need them an
#           $ORIGIN-relative path to the prefix's lib/.
#   macos   kitty-<ver>.dmg: a signed, notarised universal (x86_64 + arm64)
#           kitty.app. It is staged whole under lib/kitty/ -- touching
#           anything inside would break the signature -- and bin/* are
#           launchers into it.
#
# kitty locates its bundle from its own executable, so the launchers exec the
# real binaries rather than copying them out. Nothing is compiled.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

KITTY_VER="0.49.2"
BASE="https://github.com/kovidgoyal/kitty/releases/download/v${KITTY_VER}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# sha256 of the release assets: each one's GPG signature from Kovid Goyal's
# release key checked out, and the hash matched GitHub's digest for it (see
# recipe.yaml). Keep in step with recipe.yaml's source.artifacts.
case "$(uname -s)-$(uname -m)" in
    Linux-x86_64|Linux-amd64)
        KEY="linux"; ASSET="kitty-${KITTY_VER}-x86_64.txz"
        EXPECTED="d573618b911e9c461bd421b96c13c74c7f1cb2f1ac9c327818d4ff84366cf5c6" ;;
    Linux-aarch64|Linux-arm64)
        KEY="linux"; ASSET="kitty-${KITTY_VER}-arm64.txz"
        EXPECTED="0c81a995614426cccf0fecaf7d104d47cd63bbccf173d14976e9f92e3d60fce6" ;;
    Darwin-x86_64|Darwin-arm64)
        KEY="macos"; ASSET="kitty-${KITTY_VER}.dmg"
        EXPECTED="e524b894145d89cb76b584e3a9691196ae56bfa31d51384a44aa774252b7cef7" ;;
    *) echo "No kitty ${KITTY_VER} payload for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

# Hash from stdin: with a FILE operand that contains a backslash (a native
# Windows path) coreutils escapes the output line with a leading '\'.
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum < "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 < "$1" | awk '{print $1}'
    else
        echo "no sha256 tool found (tried sha256sum, shasum)" >&2
        return 1
    fi
}

ARCHIVE="${CVC_BUILD_DIR}/${ASSET}"
echo "Downloading ${BASE}/${ASSET}..."
curl -fSL --retry 3 -o "${ARCHIVE}" "${BASE}/${ASSET}"
ACTUAL="$(sha256_of "${ARCHIVE}")"
if [ "${ACTUAL}" != "${EXPECTED}" ]; then
    echo "sha256 mismatch for ${ASSET}: expected ${EXPECTED}, got ${ACTUAL}" >&2
    exit 1
fi
echo "sha256 OK (${ACTUAL})"

BIN="${CVC_INSTALL_DIR}/bin"
DEST="${CVC_INSTALL_DIR}/lib/kitty"
SHARE="${CVC_INSTALL_DIR}/share"
DOC="${SHARE}/doc/kitty"
rm -rf "${DEST}"
mkdir -p "${BIN}" "${DEST}" "${DOC}"
# No release archive carries a licence. licenses/ beside this script holds
# kitty's LICENSE (GPL-3.0, from the v0.49.2 tag) and upstream's SPDX SBOM
# for the release (kitty-0.49.2.tar.xz.spdx.json), which lists every
# component the binary builds bundle and its licence.
cp -p "${SCRIPT_DIR}/licenses/LICENSE" "${SCRIPT_DIR}/licenses/kitty-${KITTY_VER}.spdx.json" "${DOC}/"

# bin/<exe>: resolve the launcher's own location (following symlinks to it,
# so the prefix can move or be linked into PATH), run the preamble $2, then
# exec lib/kitty/$1<exe>.
write_launchers() {  # $1 = path under lib/kitty/ holding the binaries, $2 = preamble
    for exe in kitty kitten; do
        cat > "${BIN}/${exe}" <<EOF
#!/bin/sh
# cvcpkg kitty launcher: run ${exe} from lib/kitty in this prefix.
self=\$0
while [ -h "\$self" ]; do
    link=\$(readlink "\$self")
    case \$link in
        /*) self=\$link ;;
        *) self=\$(dirname -- "\$self")/\$link ;;
    esac
done
here=\$(CDPATH= cd -- "\$(dirname -- "\$self")" && pwd)
$2
exec "\$here/../lib/kitty/$1${exe}" "\$@"
EOF
        chmod 0755 "${BIN}/${exe}"
    done
}

if [ "${KEY}" = "linux" ]; then
    tar xJf "${ARCHIVE}" -C "${DEST}"
    [ -x "${DEST}/bin/kitty" ] || { echo "no bin/kitty in ${ASSET}" >&2; exit 1; }

    # DT_RPATH, so the bundle's own libraries win over LD_LIBRARY_PATH.
    # Upstream links bin/kitty with DT_RPATH=$ORIGIN/../lib and leaves its
    # libraries and Python extension modules without any search path; the
    # loader then searches the executable's DT_RPATH for them too, ahead of
    # LD_LIBRARY_PATH. cvcpkg's relocation pass gives every lib/**/*.so* an
    # $ORIGIN entry, which as a DT_RUNPATH would switch that off for the
    # object and let LD_LIBRARY_PATH -- the prefix's lib/ under cvcpkg's
    # activate script and its build environment -- supply cairo, FreeType,
    # HarfBuzz, libxkbcommon, OpenSSL ... from the prefix instead of the
    # bundle. So every object gets its own DT_RPATH to the bundle's lib/
    # here, which that pass keeps a DT_RPATH (the libreoffice recipe does the
    # same). bin/kitten is a Go program that needs only libc; it is left be.
    PATCHELF="$(command -v patchelf || true)"
    [ -n "${PATCHELF}" ] || { echo "patchelf is required (depends.build)" >&2; exit 1; }
    n=0
    while IFS= read -r -d '' f; do
        rp="$("${PATCHELF}" --print-rpath "${f}" 2>/dev/null)" || continue  # not ELF
        rel="${f#"${DEST}"/}"
        if [ -z "${rp}" ]; then
            case "${rel}" in
                lib/kitty-extensions/*) rp='$ORIGIN/..' ;;
                lib/*/*) echo "no rule for an RPATH-less ${rel}" >&2; exit 1 ;;
                lib/*) rp='$ORIGIN' ;;
                bin/kitten) continue ;;
                *) echo "no rule for an RPATH-less ${rel}" >&2; exit 1 ;;
            esac
        fi
        "${PATCHELF}" --force-rpath --set-rpath "${rp}" "${f}"
        n=$((n + 1))
    done < <(find "${DEST}/bin" "${DEST}/lib" -type f \( -path "${DEST}/bin/*" -o -name '*.so*' \) -print0)
    echo "DT_RPATH set on ${n} objects"

    # libxkbcommon (bundled) finds its keymap data (XKB_CONFIG_ROOT) and
    # Compose tables (XLOCALEDIR) at the host's /usr/share/X11, and the
    # prefix's libX11 its locale data at a path on the machine that built it.
    # The prefix has both: xkeyboard-config's share/X11/xkb and libx11's
    # share/X11/locale.
    write_launchers "bin/" '[ -n "${XKB_CONFIG_ROOT:-}" ] || { XKB_CONFIG_ROOT=$here/../share/X11/xkb; export XKB_CONFIG_ROOT; }
[ -n "${XLOCALEDIR:-}" ] || { XLOCALEDIR=$here/../share/X11/locale; export XLOCALEDIR; }'

    # Man pages, desktop entries and icons, copied to where a prefix
    # keeps them; the bundle keeps its own (kitty reads its share/ at run
    # time: the terminfo, the HTML docs behind F1).
    SRC_SHARE="${DEST}/share"
    mkdir -p "${SHARE}/man/man1" "${SHARE}/man/man5" "${SHARE}/applications" "${SHARE}/icons"
    cp -p "${SRC_SHARE}"/man/man1/kitty.1 "${SRC_SHARE}"/man/man1/kitten*.1 "${SHARE}/man/man1/"
    cp -p "${SRC_SHARE}/man/man5/kitty.conf.5" "${SHARE}/man/man5/"
    cp -p "${SRC_SHARE}"/applications/kitty*.desktop "${SHARE}/applications/"
    cp -pR "${SRC_SHARE}/icons/hicolor" "${SHARE}/icons/"
else
    # ditto keeps the bundle's extended attributes and code signature intact.
    MNT="${CVC_BUILD_DIR}/kitty-dmg"
    rm -rf "${MNT}" && mkdir -p "${MNT}"
    hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "${MNT}" "${ARCHIVE}"
    detach() { hdiutil detach "${MNT}" -quiet || hdiutil detach "${MNT}" -force -quiet || true; }
    trap detach EXIT
    [ -d "${MNT}/kitty.app" ] || { echo "no kitty.app in ${ASSET}" >&2; exit 1; }
    ditto "${MNT}/kitty.app" "${DEST}/kitty.app"
    detach
    trap - EXIT
    write_launchers "kitty.app/Contents/MacOS/" ""
    RES="${DEST}/kitty.app/Contents/Resources"
    mkdir -p "${SHARE}/man/man1" "${SHARE}/man/man5"
    cp -p "${RES}"/man/man1/kitty.1 "${RES}"/man/man1/kitten*.1 "${SHARE}/man/man1/"
    cp -p "${RES}/man/man5/kitty.conf.5" "${SHARE}/man/man5/"
fi

"${BIN}/kitty" --version
"${BIN}/kitten" --version
