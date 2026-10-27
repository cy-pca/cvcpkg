#!/usr/bin/env bash
# recipes/ghostty/build-macos.sh — stage the official Ghostty.app on macOS.
#
# Ghostty.dmg holds a signed, notarised universal (x86_64 + arm64) app. It is
# staged whole under lib/ghostty/ -- touching anything inside would break the
# signature -- and bin/ghostty is a launcher into it. Nothing is compiled.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

GHOSTTY_VER="1.3.1"
URL="https://release.files.ghostty.org/${GHOSTTY_VER}/Ghostty.dmg"
# sha256 of the download; the app inside is checked with codesign below. Keep
# in step with recipe.yaml's source.artifacts.
EXPECTED="18cff2b0a6cee90eead9c7d3064e808a252a40baf214aa752c1ecb793b8f5f69"
TEAM_ID="24VZTF6M5V"   # Developer ID Application: Mitchell Hashimoto
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

DMG="${CVC_BUILD_DIR}/Ghostty-${GHOSTTY_VER}.dmg"
echo "Downloading ${URL}..."
curl -fSL --retry 3 -o "${DMG}" "${URL}"
ACTUAL="$(shasum -a 256 < "${DMG}" | awk '{print $1}')"
if [ "${ACTUAL}" != "${EXPECTED}" ]; then
    echo "sha256 mismatch for Ghostty.dmg: expected ${EXPECTED}, got ${ACTUAL}" >&2
    exit 1
fi
echo "sha256 OK (${ACTUAL})"

LIBEXEC="${CVC_INSTALL_DIR}/lib/ghostty"
APP="${LIBEXEC}/Ghostty.app"
MNT="${CVC_BUILD_DIR}/dmg"
mkdir -p "${LIBEXEC}" "${MNT}"
hdiutil attach -nobrowse -noautoopen -readonly -mountpoint "${MNT}" "${DMG}" >/dev/null
detach() { hdiutil detach "${MNT}" -quiet 2>/dev/null || hdiutil detach "${MNT}" -force -quiet 2>/dev/null || true; }
trap detach EXIT
# ditto keeps the bundle's extended attributes and code signature intact.
rm -rf "${APP}"
ditto "${MNT}/Ghostty.app" "${APP}"
detach
trap - EXIT

codesign --verify --deep --strict "${APP}"
team="$(codesign -dv --verbose=2 "${APP}" 2>&1 | sed -n 's/^TeamIdentifier=//p')"
[ "${team}" = "${TEAM_ID}" ] || { echo "Ghostty.app is signed by team '${team}', want ${TEAM_ID}" >&2; exit 1; }
short="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "${APP}/Contents/Info.plist")"
[ "${short}" = "${GHOSTTY_VER}" ] || { echo "Ghostty.app is version ${short}, want ${GHOSTTY_VER}" >&2; exit 1; }
echo "Ghostty.app ${short}: signature valid, team ${team}"

# bin/ghostty: resolve the launcher's own location (following symlinks to it,
# so the prefix can move or be linked into PATH), then exec the app's binary,
# which finds its resources from its own path inside the bundle.
mkdir -p "${CVC_INSTALL_DIR}/bin"
cat > "${CVC_INSTALL_DIR}/bin/ghostty" <<'EOF'
#!/bin/sh
# cvcpkg ghostty launcher: run Ghostty.app's binary from lib/ghostty in this prefix.
self=$0
while [ -h "$self" ]; do
    link=$(readlink "$self")
    case $link in
        /*) self=$link ;;
        *) self=$(dirname -- "$self")/$link ;;
    esac
done
here=$(CDPATH= cd -- "$(dirname -- "$self")" && pwd)
exec "$here/../lib/ghostty/Ghostty.app/Contents/MacOS/ghostty" "$@"
EOF
chmod 0755 "${CVC_INSTALL_DIR}/bin/ghostty"

# Copies of what the app carries for the shell: man pages, completions, and
# the terminfo (for TERM=xterm-ghostty in programs Ghostty did not start).
RES="${APP}/Contents/Resources"
SHARE="${CVC_INSTALL_DIR}/share"
mkdir -p "${SHARE}/man/man1" "${SHARE}/man/man5" "${SHARE}/bash-completion/completions" \
         "${SHARE}/zsh/site-functions" "${SHARE}/fish/vendor_completions.d" \
         "${SHARE}/doc/ghostty"
cp -p "${RES}/man/man1/ghostty.1" "${SHARE}/man/man1/"
cp -p "${RES}/man/man5/ghostty.5" "${SHARE}/man/man5/"
cp -p "${RES}/bash-completion/completions/ghostty.bash" "${SHARE}/bash-completion/completions/"
cp -p "${RES}/zsh/site-functions/_ghostty" "${SHARE}/zsh/site-functions/"
cp -p "${RES}/fish/vendor_completions.d/ghostty.fish" "${SHARE}/fish/vendor_completions.d/"
cp -pR "${RES}/terminfo" "${SHARE}/terminfo"
cp -p "${SCRIPT_DIR}/licenses/LICENSE" "${SHARE}/doc/ghostty/LICENSE"

"${CVC_INSTALL_DIR}/bin/ghostty" --version
