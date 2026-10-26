#!/usr/bin/env bash
# recipes/wezterm-src/build.sh -- stage WezTerm's source tree, with every crate
# it builds from vendored, at src/wezterm-src/ (a source package: see
# docs/source-recipes.md). CVC_SOURCE_DIR is the release tarball with
# recipe.yaml's patches already applied.
set -euo pipefail
# shellcheck disable=SC1091
. "$(dirname "$0")/../_common/stage-source.sh"

: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
# On a Windows builder (Git Bash) the prefixes arrive as native C:\ paths;
# compare them in the form `command -v` prints.
posix() { if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; else printf '%s' "$1"; fi; }
bld="$(posix "${CVC_BUILD_PREFIX:-${CVC_DEPS_PREFIX:-}}")"
deps="$(posix "${CVC_DEPS_PREFIX:-${bld}}")"
export PATH="${bld}/bin:${deps}/bin:${PATH}"

# cargo must be the cvcpkg rust's, never a builder's own.
cargo="$(command -v cargo || true)"
case "${cargo}" in
    "${bld}"/*|"${deps}"/*) ;;
    "") echo "wezterm-src: cargo not on PATH -- is the 'rust' build dep in the closure?" >&2; exit 1 ;;
    *) echo "wezterm-src: refusing cargo at ${cargo}, outside the cvcpkg prefixes" >&2; exit 1 ;;
esac
"${cargo}" --version

# The download, done once: every crate Cargo.lock names, checked against the
# checksums it records (--locked refuses any change to the lockfile). A
# private cargo home keeps the builder's ~/.cargo out of it.
export CARGO_HOME="${CVC_BUILD_DIR}/cargo-home"
unset RUSTUP_TOOLCHAIN
(cd "${CVC_SOURCE_DIR}" && "${cargo}" vendor --locked --versioned-dirs vendor \
    > "${CVC_BUILD_DIR}/vendor-config.toml")
# Point cargo at vendor/. WezTerm already has .cargo/config (its target
# rustflags); with a .cargo/config.toml beside it cargo would read only one of
# the two, so the source replacement goes into the existing file.
{
    printf '\n# Added by cvcpkg recipes/wezterm-src: build from vendor/, offline.\n'
    cat "${CVC_BUILD_DIR}/vendor-config.toml"
} >> "${CVC_SOURCE_DIR}/.cargo/config"
grep -q 'replace-with = "vendored-sources"' "${CVC_SOURCE_DIR}/.cargo/config" \
    || { echo "wezterm-src: cargo vendor printed no source replacement" >&2; exit 1; }

cvc_stage_source
# The tree's one symlink, harfbuzz's README -> README.md, is documentation, and a
# symlink in a bundle is the one thing a Windows consumer may fail to extract.
rm -f "${CVC_INSTALL_DIR}/src/wezterm-src/deps/harfbuzz/harfbuzz/README"
