#!/usr/bin/env bash
# recipes/wezterm/build.sh — build WezTerm from source on linux and macOS.
# Windows uses build.ps1.
#
# cargo builds wezterm, wezterm-gui, wezterm-mux-server and strip-ansi-escapes
# from the wezterm-src source package (CVC_SOURCE_DIR: the release's -src tree,
# with patches/ applied) with the cvcpkg rust toolchain and crates,
# offline. freetype, harfbuzz, cairo, libssh, libssh2 and Lua are compiled in
# from WezTerm's bundled sources; OpenSSL comes from the prefix, and on linux
# so do X11/xcb/xkbcommon/wayland/fontconfig/zlib (runtime dependencies). The
# binaries are staged in lib/wezterm/ and bin/* are launchers for them: wezterm
# finds wezterm-gui next to its own executable, and on linux the launchers
# point xkbcommon at the prefix's XKB and Compose data first (see the notes).
#
# Nothing is downloaded: every crate is a cvcpkg package (the wezterm-crates
# set), every library comes from the prefix.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="${CVC_SOURCE_DIR}"

BIN="${CVC_INSTALL_DIR}/bin"
LIBEXEC="${CVC_INSTALL_DIR}/lib/wezterm"
SHARE="${CVC_INSTALL_DIR}/share"
DOC="${SHARE}/doc/wezterm"
mkdir -p "${BIN}" "${LIBEXEC}" "${DOC}"
# WezTerm's licence and those of the fonts every build embeds, from the source
# tree (ANGLE ships only on Windows; build.ps1 adds its licence).
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

case "$(uname -s)" in
    Linux) OS=linux ;;
    Darwin) OS=macos ;;
    *) echo "wezterm: no build for $(uname -s)" >&2; exit 1 ;;
esac

# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"    # cc for the bundled C, CVC_JOBS
deps="${CVC_DEPS_PREFIX:-${CVC_INSTALL_DIR}}"
bld="${CVC_BUILD_PREFIX:-${deps}}"
export PATH="${bld}/bin:${deps}/bin:${PATH}"

# The toolchain must be the one cvcpkg installed: a builder's own rustup would
# compile the artifact with something no manifest records.
tools="cargo rustc"
[ "${OS}" = linux ] && tools="${tools} pkg-config"
for tool in ${tools}; do
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

# cargo's state stays in the build tree; a $HOME/.cargo on the builder would be
# both an input and an output otherwise.
export CARGO_HOME="${CVC_BUILD_DIR}/cargo-home"
export RUSTUP_HOME="${CVC_BUILD_DIR}/rustup-home"
unset RUSTUP_TOOLCHAIN
export CARGO_TARGET_DIR="${CVC_BUILD_DIR}/cargo-target"
export CARGO_NET_OFFLINE=true
mkdir -p "${CARGO_HOME}" "${RUSTUP_HOME}"

# Crates: each is a cvcpkg package staged at share/cargo/crates/ beside the
# wezterm-crates set, whose cargo-config.toml.in replaces crates.io and the
# one git dependency with that directory (docs/rust-crates.md). Offline makes
# a crate missing from it an error rather than a download.
crates_set=""
for root in "${bld}" "${deps}"; do
    if [ -f "${root}/share/cargo/sets/wezterm-crates/cargo-config.toml.in" ]; then
        crates_set="${root}/share/cargo/sets/wezterm-crates"
        crates_dir="${root}/share/cargo/crates"
        break
    fi
done
[ -n "${crates_set}" ] \
    || { echo "wezterm: no wezterm-crates set in ${bld} or ${deps} -- is it in the closure?" >&2; exit 1; }
sed "s|@CRATES_DIR@|${crates_dir}|" "${crates_set}/cargo-config.toml.in" > "${CARGO_HOME}/config.toml"
echo "wezterm: crates from ${crates_dir} ($(ls "${crates_dir}" | wc -l | tr -d ' ') packages)"

# Every library comes from the prefix. openssl-sys is pointed at its OpenSSL;
# WezTerm turns on openssl's `vendored` feature on macOS (and Windows), and
# NO_VENDOR overrides that. On linux pkg-config sees only the prefix's .pc
# files.
export OPENSSL_DIR="${deps}" OPENSSL_NO_VENDOR=1
if [ "${OS}" = linux ]; then
    PKG_CONFIG="$(command -v pkg-config)"
    export PKG_CONFIG
    export PKG_CONFIG_LIBDIR="${deps}/lib/pkgconfig:${deps}/share/pkgconfig"
    unset PKG_CONFIG_PATH
fi
# Strip symbols at link time (upstream's packages are stripped as well).
export CARGO_PROFILE_RELEASE_STRIP=symbols

# distro-defaults: check_for_updates defaults to false -- a pinned package
# should not tell its user to go and download another version.
(cd "${SRC}" && "${CARGO}" build --release --locked --offline --jobs "${CVC_JOBS}" \
    -p wezterm -p wezterm-gui -p wezterm-mux-server -p strip-ansi-escapes \
    --features wezterm-gui/distro-defaults)

for exe in ${EXES}; do
    cp -p "${CARGO_TARGET_DIR}/release/${exe}" "${LIBEXEC}/${exe}"
done

mkdir -p "${SHARE}/bash-completion/completions" "${SHARE}/zsh/site-functions" \
         "${SHARE}/fish/vendor_completions.d"
cp -p "${SRC}/assets/shell-completion/bash" "${SHARE}/bash-completion/completions/wezterm"
cp -p "${SRC}/assets/shell-completion/zsh" "${SHARE}/zsh/site-functions/_wezterm"
cp -p "${SRC}/assets/shell-completion/fish" "${SHARE}/fish/vendor_completions.d/wezterm.fish"

if [ "${OS}" = linux ]; then
    # libxkbcommon finds its keymap data (XKB_CONFIG_ROOT) and Compose tables
    # (XLOCALEDIR) at paths fixed when it was built, which do not exist in an
    # installed prefix, and wezterm-gui cannot start without both (segfault,
    # "no keymap", "Failed to acquire compose table"). The prefix has them:
    # xkeyboard-config's share/X11/xkb and libx11's share/X11/locale.
    write_launchers "" '[ -n "${XKB_CONFIG_ROOT:-}" ] || { XKB_CONFIG_ROOT=$here/../share/X11/xkb; export XKB_CONFIG_ROOT; }
[ -n "${XLOCALEDIR:-}" ] || { XLOCALEDIR=$here/../share/X11/locale; export XLOCALEDIR; }'

    # Desktop entry, icon and AppStream metadata, where upstream's own packages
    # put them (ci/deploy.sh). Left out: the opt-in shell integration a system
    # install sources from /etc/profile.d, and the Nautilus extension with its
    # open-wezterm-here helper.
    mkdir -p "${SHARE}/applications" "${SHARE}/icons/hicolor/128x128/apps" "${SHARE}/metainfo"
    cp -p "${SRC}/assets/wezterm.desktop" "${SHARE}/applications/org.wezfurlong.wezterm.desktop"
    cp -p "${SRC}/assets/icon/terminal.png" \
          "${SHARE}/icons/hicolor/128x128/apps/org.wezfurlong.wezterm.png"
    cp -p "${SRC}/assets/wezterm.appdata.xml" "${SHARE}/metainfo/org.wezfurlong.wezterm.appdata.xml"
else
    write_launchers "" ""
fi

# The staged CLI must start; test.sh exercises the rest. (Until the relocation
# pass gives it a RUNPATH, the linux build finds OpenSSL 3 in the prefix only
# through LD_LIBRARY_PATH.)
LD_LIBRARY_PATH="${deps}/lib${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}" "${BIN}/wezterm" --version
