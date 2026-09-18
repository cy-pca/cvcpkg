#!/bin/sh
# cvcpkg installer — https://cvcpkg.org/install.sh
#
#   curl -fsSL https://cvcpkg.org/install.sh | sh
#
# To pass options, use the POSIX `sh -s --` form:
#
#   curl -fsSL https://cvcpkg.org/install.sh | sh -s -- --install-dir /opt/cvcpkg/bin
#
# Downloads the latest cvcpkg standalone binary release for your
# platform, verifies its sha256 checksum, and installs it to the
# chosen directory (default: $HOME/.local/bin).
#
# Options:
#   --install-dir DIR   install location (default: $HOME/.local/bin)
#   --version TAG       pin a release tag, e.g. cvcpkg-v2.0.0
#   -h, --help          show usage and exit
#
# Env overrides:
#   CVCPKG_VERSION      pin a release tag, e.g. cvcpkg-v2.0.0
#   CVCPKG_INSTALL_DIR  install location (default: $HOME/.local/bin)
#   CVCPKG_REPO         GitHub repo to fetch releases from
#                       (default: cy-pca/cvcpkg)
#
# Precedence: command-line flag > environment variable > built-in default.

set -eu

say() { printf '%s\n' "$*" >&2; }
die() { say "error: $*"; exit 1; }

REPO="${CVCPKG_REPO:-cy-pca/cvcpkg}"

install_dir_flag=""

usage() {
    cat >&2 <<'EOF'
cvcpkg installer

Usage:
  curl -fsSL https://cvcpkg.org/install.sh | sh
  curl -fsSL https://cvcpkg.org/install.sh | sh -s -- [options]

Options:
  --install-dir DIR   install cvcpkg into DIR (default: $HOME/.local/bin)
  --version TAG       install a specific release, e.g. cvcpkg-v2.0.0
  -h, --help          show this help and exit

Environment:
  CVCPKG_INSTALL_DIR  install location (default: $HOME/.local/bin)
  CVCPKG_VERSION      pin a release tag, e.g. cvcpkg-v2.0.0
  CVCPKG_REPO         GitHub repo to fetch releases from (default: cy-pca/cvcpkg)

Precedence: command-line flag > environment variable > built-in default.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --install-dir)
            [ $# -ge 2 ] || die "--install-dir requires a directory argument"
            install_dir_flag="$2"
            shift 2
            ;;
        --install-dir=*)
            install_dir_flag="${1#--install-dir=}"
            shift
            ;;
        --version)
            [ $# -ge 2 ] || die "--version requires a tag argument"
            CVCPKG_VERSION="$2"
            shift 2
            ;;
        --version=*)
            CVCPKG_VERSION="${1#--version=}"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "unknown option '$1' (try --help)"
            ;;
    esac
done

INSTALL_DIR="${install_dir_flag:-${CVCPKG_INSTALL_DIR:-$HOME/.local/bin}}"
case "$INSTALL_DIR" in
    "~")   INSTALL_DIR="$HOME" ;;
    "~/"*) INSTALL_DIR="$HOME/${INSTALL_DIR#~/}" ;;
    /*)    ;;
    *)     INSTALL_DIR="$(pwd)/$INSTALL_DIR" ;;
esac

command -v curl >/dev/null 2>&1 || die "curl is required but not found"

# ---- OS / arch detection ----------------------------------------------
os="$(uname -s)"
arch="$(uname -m)"

case "$os" in
    Linux)   platform=linux ;;
    Darwin)  platform=macos ;;
    FreeBSD) platform=freebsd ;;
    OpenBSD) platform=openbsd ;;
    NetBSD)  platform=netbsd ;;
    *) die "unsupported OS '$os' — try 'pip install cvcpkg' instead" ;;
esac

case "$arch" in
    x86_64|amd64)  arch=x86_64 ;;
    arm64|aarch64) arch=arm64 ;;
    *) die "unsupported architecture '$arch' — try 'pip install cvcpkg' instead" ;;
esac

case "$platform-$arch" in
    linux-x86_64)   asset=cvcpkg-linux-x86_64 ;;
    macos-arm64)    asset=cvcpkg-macos-arm64 ;;
    freebsd-x86_64) asset=cvcpkg-freebsd-x86_64 ;;
    openbsd-x86_64) asset=cvcpkg-openbsd-x86_64 ;;
    netbsd-x86_64)  asset=cvcpkg-netbsd-x86_64 ;;
    *)
        die "no prebuilt binary for $platform/$arch yet — try 'pip install cvcpkg' instead"
        ;;
esac

# ---- Resolve release tag ----------------------------------------------
if [ -n "${CVCPKG_VERSION:-}" ]; then
    tag="$CVCPKG_VERSION"
else
    say "Resolving the latest cvcpkg release..."
    tag="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases" \
        | grep -o '"tag_name": *"cvcpkg-v[0-9][^"]*"' \
        | grep -v -- '-rc' \
        | head -n1 \
        | sed -E 's/.*"(cvcpkg-v[^"]+)".*/\1/')"
    [ -n "$tag" ] || die "could not resolve the latest cvcpkg release — set CVCPKG_VERSION=cvcpkg-vX.Y.Z"
fi

say "Installing cvcpkg ($tag) for $platform/$arch..."

base_url="https://github.com/${REPO}/releases/download/${tag}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT INT TERM

curl -fsSL -o "$tmp/$asset" "${base_url}/${asset}" \
    || die "download failed: ${base_url}/${asset}"
curl -fsSL -o "$tmp/$asset.sha256" "${base_url}/${asset}.sha256" \
    || die "checksum download failed: ${base_url}/${asset}.sha256"

# ---- Verify checksum ----------------------------------------------------
expected="$(awk '{print $1}' "$tmp/$asset.sha256")"
if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$tmp/$asset" | awk '{print $1}')"
elif command -v shasum >/dev/null 2>&1; then
    actual="$(shasum -a 256 "$tmp/$asset" | awk '{print $1}')"
else
    die "neither sha256sum nor shasum found — cannot verify the download"
fi
[ "$expected" = "$actual" ] \
    || die "checksum mismatch for $asset (expected $expected, got $actual)"

# ---- Install ------------------------------------------------------------
if ! mkdir -p "$INSTALL_DIR" 2>/dev/null || [ ! -w "$INSTALL_DIR" ]; then
    die "cannot write to $INSTALL_DIR — re-run with a writable --install-dir, or: curl -fsSL https://cvcpkg.org/install.sh | sudo sh -s -- --install-dir $INSTALL_DIR"
fi
chmod +x "$tmp/$asset"
mv "$tmp/$asset" "$INSTALL_DIR/cvcpkg"

say "cvcpkg installed to $INSTALL_DIR/cvcpkg"

case ":$PATH:" in
    *":$INSTALL_DIR:"*) ;;
    *)
        say ""
        say "  $INSTALL_DIR is not on your PATH. Add it with:"
        say ""
        say "    export PATH=\"$INSTALL_DIR:\$PATH\""
        say ""
        ;;
esac

"$INSTALL_DIR/cvcpkg" --version || true
