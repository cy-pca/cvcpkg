#!/usr/bin/env bash
# recipes/wezterm/build.sh — WezTerm on linux (built from source) and macOS
# (upstream's signed app). Windows uses build.ps1.
#
#   linux  cargo builds wezterm, wezterm-gui, wezterm-mux-server and
#          strip-ansi-escapes from the wezterm-src source package (the
#          release's -src tarball, in CVC_SOURCE_DIR with patches/ applied),
#          with the cvcpkg rust toolchain. They link X11/xcb/xkbcommon/
#          wayland/fontconfig/openssl/zlib from the prefix (runtime
#          dependencies); freetype, harfbuzz, cairo, libssh, libssh2 and Lua
#          are compiled in from WezTerm's bundled sources. They are staged in
#          lib/wezterm/, and bin/* are launchers that point xkbcommon at the
#          prefix's XKB and Compose data first (see the recipe notes).
#   macos  WezTerm-macos-<tag>.zip: a signed, notarised universal (x86_64 +
#          arm64) WezTerm.app, not built here. It is staged whole under
#          lib/wezterm/ -- touching anything inside would break the
#          signature -- and bin/* are launchers into it.
#
# Either way wezterm finds wezterm-gui next to its own executable, so the
# launchers exec the real binaries rather than copying them out.
#
# HERMETICITY, STATED PLAINLY: Cargo.lock pins every crate (by version and
# checksum, and the one git dependency by commit), but cargo FETCHES them from
# crates.io and github.com: the release tarball does not vendor crate
# sources. Set CVC_CARGO_HOME to a pre-seeded cargo home plus
# CVC_CARGO_OFFLINE=1 to build air-gapped.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"

WEZTERM_TAG="20240203-110809-5046fc22"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="${CVC_SOURCE_DIR}"

BIN="${CVC_INSTALL_DIR}/bin"
LIBEXEC="${CVC_INSTALL_DIR}/lib/wezterm"
SHARE="${CVC_INSTALL_DIR}/share"
DOC="${SHARE}/doc/wezterm"
mkdir -p "${BIN}" "${LIBEXEC}" "${DOC}"
# WezTerm's licence and those of the fonts every build embeds, from the source
# tree (ANGLE and ConPTY ship only on Windows; build.ps1 adds theirs).
cp -p "${SRC}/LICENSE.md" "${SRC}/assets/fonts/LICENSE_OFL.txt" \
      "${SRC}/assets/fonts/LICENSE_POWERLINE_EXTRA.txt" "${DOC}/"

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

build_linux() {
    # shellcheck disable=SC1090
    source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # cc for the bundled C, CVC_JOBS
    local deps="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
    local bld="${CVC_BUILD_PREFIX:-${deps}}"
    export PATH="${bld}/bin:${deps}/bin:${PATH}"

    # The toolchain must be the one cvcpkg installed: a builder's own rustup
    # would compile the artifact with something no manifest records.
    local tool resolved
    for tool in cargo rustc pkg-config; do
        resolved="$(command -v "${tool}" 2>/dev/null || true)"
        case "${resolved}" in
            "${bld}"/*|"${deps}"/*) ;;
            "") echo "wezterm: ${tool} not on PATH -- is its build dep in the closure?" >&2; exit 1 ;;
            *) echo "wezterm: refusing ${tool} at ${resolved}, outside the cvcpkg prefixes" >&2; exit 1 ;;
        esac
    done
    CARGO="$(command -v cargo)"
    RUSTC="$(command -v rustc)"
    export CARGO RUSTC
    "${CARGO}" --version
    "${RUSTC}" --version

    # cargo's state stays in the build tree; a $HOME/.cargo on the builder
    # would be both an input and an output otherwise.
    export CARGO_HOME="${CVC_CARGO_HOME:-${CVC_BUILD_DIR}/cargo-home}"
    export RUSTUP_HOME="${CVC_BUILD_DIR}/rustup-home"
    unset RUSTUP_TOOLCHAIN
    export CARGO_TARGET_DIR="${CVC_BUILD_DIR}/cargo-target"
    mkdir -p "${CARGO_HOME}" "${RUSTUP_HOME}"
    if [ -n "${CVC_CARGO_OFFLINE:-}" ]; then
        export CARGO_NET_OFFLINE=true
    else
        export CARGO_NET_OFFLINE=false
        echo "wezterm: NOTE -- cargo will FETCH the crates Cargo.lock pins (see the header)."
    fi

    # Every library comes from the prefix: pkg-config sees only its .pc files,
    # and openssl-sys is pointed at its OpenSSL (WezTerm vendors OpenSSL only
    # on Windows and macOS; NO_VENDOR keeps it that way here too).
    export PKG_CONFIG="$(command -v pkg-config)"
    export PKG_CONFIG_LIBDIR="${deps}/lib/pkgconfig:${deps}/share/pkgconfig"
    unset PKG_CONFIG_PATH
    export OPENSSL_DIR="${deps}" OPENSSL_NO_VENDOR=1
    # Strip symbols at link time (upstream's packages are stripped as well).
    export CARGO_PROFILE_RELEASE_STRIP=symbols

    # distro-defaults: check_for_updates defaults to false -- a pinned package
    # should not tell its user to go and download another version.
    (cd "${SRC}" && "${CARGO}" build --release --locked --jobs "${CVC_JOBS}" \
        -p wezterm -p wezterm-gui -p wezterm-mux-server -p strip-ansi-escapes \
        --features wezterm-gui/distro-defaults)

    for exe in ${EXES}; do
        cp -p "${CARGO_TARGET_DIR}/release/${exe}" "${LIBEXEC}/${exe}"
    done
    # libxkbcommon finds its keymap data (XKB_CONFIG_ROOT) and Compose tables
    # (XLOCALEDIR) at paths fixed when it was built, which do not exist in an
    # installed prefix, and wezterm-gui cannot start without both (segfault,
    # "no keymap", "Failed to acquire compose table"). The prefix has them:
    # xkeyboard-config's share/X11/xkb and libx11's share/X11/locale.
    write_launchers "" '[ -n "${XKB_CONFIG_ROOT:-}" ] || { XKB_CONFIG_ROOT=$here/../share/X11/xkb; export XKB_CONFIG_ROOT; }
[ -n "${XLOCALEDIR:-}" ] || { XLOCALEDIR=$here/../share/X11/locale; export XLOCALEDIR; }'

    # Desktop entry, icon, AppStream metadata and shell completions, where
    # upstream's own packages put them (ci/deploy.sh). Left out: the opt-in
    # shell integration a system install sources from /etc/profile.d, and the
    # Nautilus extension with its open-wezterm-here helper.
    mkdir -p "${SHARE}/applications" "${SHARE}/icons/hicolor/128x128/apps" \
             "${SHARE}/metainfo" "${SHARE}/bash-completion/completions" \
             "${SHARE}/zsh/site-functions" "${SHARE}/fish/vendor_completions.d"
    cp -p "${SRC}/assets/wezterm.desktop" "${SHARE}/applications/org.wezfurlong.wezterm.desktop"
    cp -p "${SRC}/assets/icon/terminal.png" \
          "${SHARE}/icons/hicolor/128x128/apps/org.wezfurlong.wezterm.png"
    cp -p "${SRC}/assets/wezterm.appdata.xml" "${SHARE}/metainfo/org.wezfurlong.wezterm.appdata.xml"
    cp -p "${SRC}/assets/shell-completion/bash" "${SHARE}/bash-completion/completions/wezterm"
    cp -p "${SRC}/assets/shell-completion/zsh" "${SHARE}/zsh/site-functions/_wezterm"
    cp -p "${SRC}/assets/shell-completion/fish" "${SHARE}/fish/vendor_completions.d/wezterm.fish"
}

# Hash from stdin: with a FILE operand that contains a backslash (a native
# Windows path) coreutils escapes the output line with a leading '\'.
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum < "$1" | awk '{print $1}'
    else
        shasum -a 256 < "$1" | awk '{print $1}'
    fi
}

stage_macos() {
    # sha256 as published beside the asset (<asset>.sha256), re-hashed after
    # download. Keep in step with recipe.yaml.
    local asset="WezTerm-macos-${WEZTERM_TAG}.zip"
    local expected="e77388cad55f2e9da95a220a89206a6c58f865874a629b7c3ea3c162f5692224"
    local archive="${CVC_BUILD_DIR}/${asset}" actual
    curl -fSL --retry 3 -o "${archive}" \
        "https://github.com/wez/wezterm/releases/download/${WEZTERM_TAG}/${asset}"
    actual="$(sha256_of "${archive}")"
    [ "${actual}" = "${expected}" ] \
        || { echo "sha256 mismatch for ${asset}: expected ${expected}, got ${actual}" >&2; exit 1; }
    # ditto keeps the bundle's extended attributes and code signature intact.
    local unzipped="${CVC_BUILD_DIR}/macos"
    rm -rf "${unzipped}"
    ditto -x -k "${archive}" "${unzipped}"
    ditto "${unzipped}/WezTerm-macos-${WEZTERM_TAG}/WezTerm.app" "${LIBEXEC}/WezTerm.app"
    write_launchers "WezTerm.app/Contents/MacOS/" ""
    mkdir -p "${SHARE}/bash-completion/completions" "${SHARE}/zsh/site-functions" \
             "${SHARE}/fish/vendor_completions.d"
    cp -p "${SRC}/assets/shell-completion/bash" "${SHARE}/bash-completion/completions/wezterm"
    cp -p "${SRC}/assets/shell-completion/zsh" "${SHARE}/zsh/site-functions/_wezterm"
    cp -p "${SRC}/assets/shell-completion/fish" "${SHARE}/fish/vendor_completions.d/wezterm.fish"
}

case "$(uname -s)" in
    Linux) build_linux ;;
    Darwin) stage_macos ;;
    *) echo "wezterm: no build for $(uname -s)" >&2; exit 1 ;;
esac

# The staged CLI must start; test.sh exercises the rest. (Until the relocation
# pass gives it a RUNPATH, the linux build finds OpenSSL 3 in the prefix only
# through LD_LIBRARY_PATH.)
LD_LIBRARY_PATH="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" \
    "${BIN}/wezterm" --version
