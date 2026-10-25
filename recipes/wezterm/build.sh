#!/usr/bin/env bash
# recipes/wezterm/build.sh — download and stage the official WezTerm binaries
# on linux-x86_64 and macOS. Windows uses build.ps1.
#
#   linux-x86_64  wezterm-<tag>.Ubuntu22.04.tar.xz: upstream's .deb payload as
#                 a tarball (usr/bin, usr/share). The executables link
#                 X11/xcb/xkbcommon/wayland/fontconfig/openssl/zlib from the
#                 system; this recipe declares each of those as a runtime
#                 dependency, and cvcpkg's relocation pass gives them an
#                 $ORIGIN-relative RUNPATH that finds them in the prefix. They
#                 are staged in lib/wezterm/, and bin/* are launchers that
#                 point xkbcommon at the prefix's XKB and Compose data first
#                 (see the recipe notes).
#   macos         WezTerm-macos-<tag>.zip: a signed, notarised universal
#                 (x86_64 + arm64) WezTerm.app. It is staged whole under
#                 lib/wezterm/ -- touching anything inside would break the
#                 signature -- and bin/* are launchers into it.
#
# Either way wezterm finds wezterm-gui next to its own executable, so the
# launchers exec the real binaries rather than copying them out. Nothing is
# compiled.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

WEZTERM_TAG="20240203-110809-5046fc22"
BASE="https://github.com/wez/wezterm/releases/download/${WEZTERM_TAG}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

case "$(uname -s)-$(uname -m)" in
    Linux-x86_64|Linux-amd64) KEY="linux-x86_64" ;;
    Darwin-x86_64|Darwin-arm64) KEY="macos" ;;
    *) echo "No WezTerm ${WEZTERM_TAG} payload for $(uname -s)-$(uname -m)" >&2; exit 1 ;;
esac

# sha256 of the release assets, as published beside each one (<asset>.sha256)
# and re-hashed after download. Keep in step with recipe.yaml's
# source.artifacts and build.ps1's $expected.
case "${KEY}" in
    linux-x86_64)
        ASSET="wezterm-${WEZTERM_TAG}.Ubuntu22.04.tar.xz"
        EXPECTED="142cb46de648200b852cc4b1d932e6b8f2a9d3c55f6dc5af44b6d4eeb9385f5c" ;;
    macos)
        ASSET="WezTerm-macos-${WEZTERM_TAG}.zip"
        EXPECTED="e77388cad55f2e9da95a220a89206a6c58f865874a629b7c3ea3c162f5692224" ;;
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
LIBEXEC="${CVC_INSTALL_DIR}/lib/wezterm"
DOC="${CVC_INSTALL_DIR}/share/doc/wezterm"
mkdir -p "${BIN}" "${LIBEXEC}" "${DOC}"
# No release archive carries a licence; licenses/ beside this script holds
# WezTerm's own and those of what its builds bundle, copied from the upstream
# repositories at the pinned tags. ANGLE and ConPTY ship only on Windows.
for f in LICENSE.md LICENSE_OFL.txt LICENSE_POWERLINE_EXTRA.txt; do
    cp -p "${SCRIPT_DIR}/licenses/${f}" "${DOC}/"
done

EXES="wezterm wezterm-gui wezterm-mux-server strip-ansi-escapes"

# bin/<exe>: resolve the launcher's own location (following symlinks to it,
# so the prefix can move or be linked into PATH), run the preamble $2, then
# exec lib/wezterm/$1<exe>.
write_launchers() {  # $1 = path under lib/wezterm/ holding the binaries, $2 = preamble
    for exe in ${EXES}; do
        cat > "${BIN}/${exe}" <<EOF
#!/bin/sh
# cvcpkg wezterm launcher: run ${exe} from lib/wezterm in this prefix.
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
exec "\$here/../lib/wezterm/$1${exe}" "\$@"
EOF
        chmod 0755 "${BIN}/${exe}"
    done
}

if [ "${KEY}" = "linux-x86_64" ]; then
    tar xJf "${ARCHIVE}" -C "${CVC_BUILD_DIR}"
    SRC="${CVC_BUILD_DIR}/wezterm/usr"
    for exe in ${EXES}; do
        cp -p "${SRC}/bin/${exe}" "${LIBEXEC}/${exe}"
    done
    # libxkbcommon finds its keymap data (XKB_CONFIG_ROOT) and Compose tables
    # (XLOCALEDIR) at paths fixed when it was built, which do not exist in an
    # installed prefix, and wezterm-gui cannot start without both (segfault,
    # "no keymap", "Failed to acquire compose table"). The prefix has them:
    # xkeyboard-config's share/X11/xkb and libx11's share/X11/locale.
    write_launchers "" '[ -n "${XKB_CONFIG_ROOT:-}" ] || { XKB_CONFIG_ROOT=$here/../share/X11/xkb; export XKB_CONFIG_ROOT; }
[ -n "${XLOCALEDIR:-}" ] || { XLOCALEDIR=$here/../share/X11/locale; export XLOCALEDIR; }'
    # Desktop entry, icon, AppStream metadata and shell completions. Left out:
    # DEBIAN/, etc/profile.d/wezterm.sh (opt-in shell integration that a
    # system install sources for every login shell), the Nautilus extension
    # and its open-wezterm-here helper -- all desktop-install glue.
    SHARE="${CVC_INSTALL_DIR}/share"
    mkdir -p "${SHARE}/applications" "${SHARE}/icons/hicolor/128x128/apps" \
             "${SHARE}/metainfo" "${SHARE}/bash-completion/completions" \
             "${SHARE}/zsh/site-functions"
    cp -p "${SRC}/share/applications/org.wezfurlong.wezterm.desktop" "${SHARE}/applications/"
    cp -p "${SRC}/share/icons/hicolor/128x128/apps/org.wezfurlong.wezterm.png" \
          "${SHARE}/icons/hicolor/128x128/apps/"
    cp -p "${SRC}/share/metainfo/org.wezfurlong.wezterm.appdata.xml" "${SHARE}/metainfo/"
    cp -p "${SRC}/share/bash-completion/completions/wezterm" "${SHARE}/bash-completion/completions/"
    cp -p "${SRC}/share/zsh/functions/Completion/Unix/_wezterm" "${SHARE}/zsh/site-functions/"
else
    # ditto keeps the bundle's extended attributes and code signature intact.
    UNZIPPED="${CVC_BUILD_DIR}/macos"
    rm -rf "${UNZIPPED}"
    ditto -x -k "${ARCHIVE}" "${UNZIPPED}"
    APP="${UNZIPPED}/WezTerm-macos-${WEZTERM_TAG}/WezTerm.app"
    ditto "${APP}" "${LIBEXEC}/WezTerm.app"
    write_launchers "WezTerm.app/Contents/MacOS/" ""
    RES="${LIBEXEC}/WezTerm.app/Contents/Resources/shell-completion"
    mkdir -p "${CVC_INSTALL_DIR}/share/bash-completion/completions" \
             "${CVC_INSTALL_DIR}/share/zsh/site-functions" \
             "${CVC_INSTALL_DIR}/share/fish/vendor_completions.d"
    cp -p "${RES}/bash" "${CVC_INSTALL_DIR}/share/bash-completion/completions/wezterm"
    cp -p "${RES}/zsh" "${CVC_INSTALL_DIR}/share/zsh/site-functions/_wezterm"
    cp -p "${RES}/fish" "${CVC_INSTALL_DIR}/share/fish/vendor_completions.d/wezterm.fish"
fi

# On linux this only proves the CLI starts (wezterm links no GUI libraries);
# test.sh runs wezterm-gui with the prefix's runtime dependencies.
"${BIN}/wezterm" --version
