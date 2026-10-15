#!/bin/sh
# cvcpkg installer — https://cvcpkg.org/install.sh
#
#   curl -fsSL https://cvcpkg.org/install.sh | sh
#
# To pass options, use the POSIX `sh -s --` form:
#
#   curl -fsSL https://cvcpkg.org/install.sh | sh -s -- --install-dir /opt/cvcpkg/bin
#
# Installs the newest `cvcpkg` package that the cvcpkg registry serving this
# script has published for your platform (its bin/cvcpkg), verified against
# the sha256 the registry recorded for it.  Each deployment bakes its own URL
# in when it serves this file, so a fork's /install.sh installs the fork's
# own builds.  Only when that registry has no cvcpkg for your platform (or
# for the pinned version) does it fall back to the standalone binary attached
# to the GitHub release, verified against the release's .sha256.
#
# Options:
#   --install-dir DIR   install location (default: $HOME/.local/bin)
#   --version VERSION   pin a version, e.g. 2.3.0 (a cvcpkg-v2.3.0 tag works too)
#   --server URL        cvcpkg registry to install from
#   -h, --help          show usage and exit
#
# Env overrides:
#   CVCPKG_INSTALL_DIR  install location (default: $HOME/.local/bin)
#   CVCPKG_VERSION      pin a version, as --version
#   CVCPKG_SERVER_URL   registry to install from, as --server
#                       (default: the registry that served this script)
#   CVCPKG_TOKEN        bearer token, for a registry that requires auth for
#                       reads; sent to that registry only
#   CVCPKG_REPO         GitHub repo to fall back to (default: cy-pca/cvcpkg)
#
# Precedence: command-line flag > environment variable > built-in default.

set -eu

say() { printf '%s\n' "$*" >&2; }
die() { say "error: $*"; exit 1; }

# Baked in by the cvcpkg server that serves this script (its CVCPKG_SITE_URL
# and CVCPKG_GITHUB_REPO), so every deployment hands out an installer for its
# own builds.  As committed, these are the official deployment's.
DEFAULT_SERVER_URL="https://cvcpkg.org"
DEFAULT_GITHUB_REPO="cy-pca/cvcpkg"

install_dir_flag=""
server_flag=""

usage() {
    cat >&2 <<EOF
cvcpkg installer

Usage:
  curl -fsSL $DEFAULT_SERVER_URL/install.sh | sh
  curl -fsSL $DEFAULT_SERVER_URL/install.sh | sh -s -- [options]

Installs the newest cvcpkg the registry has published for this platform,
or, if it has none, the latest GitHub release of $DEFAULT_GITHUB_REPO.

Options:
  --install-dir DIR   install cvcpkg into DIR (default: \$HOME/.local/bin)
  --version VERSION   install a specific version, e.g. 2.3.0 or cvcpkg-v2.3.0
  --server URL        registry to install from (default: $DEFAULT_SERVER_URL)
  -h, --help          show this help and exit

Environment:
  CVCPKG_INSTALL_DIR  install location (default: \$HOME/.local/bin)
  CVCPKG_VERSION      pin a version, as --version
  CVCPKG_SERVER_URL   registry to install from, as --server
  CVCPKG_TOKEN        bearer token, for a registry that requires auth for reads
  CVCPKG_REPO         GitHub repo to fall back to (default: $DEFAULT_GITHUB_REPO)

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
            [ $# -ge 2 ] || die "--version requires a version argument"
            CVCPKG_VERSION="$2"
            shift 2
            ;;
        --version=*)
            CVCPKG_VERSION="${1#--version=}"
            shift
            ;;
        --server)
            [ $# -ge 2 ] || die "--server requires a URL argument"
            server_flag="$2"
            shift 2
            ;;
        --server=*)
            server_flag="${1#--server=}"
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

SERVER_URL="${server_flag:-${CVCPKG_SERVER_URL:-$DEFAULT_SERVER_URL}}"
SERVER_URL="${SERVER_URL%/}"
REPO="${CVCPKG_REPO:-$DEFAULT_GITHUB_REPO}"

# One pin serves both sources: the registry versions cvcpkg as 2.3.0 (plus a
# +cvc.N suffix for repackagings), GitHub tags the release cvcpkg-v2.3.0.
version="${CVCPKG_VERSION:-}"
version="${version#cvcpkg-}"
version="${version#v}"

command -v curl >/dev/null 2>&1 || die "curl is required but not found"

# ---- OS / arch detection (cvcpkg's own platform / arch names) -----------
os="$(uname -s)"
arch="$(uname -m)"

case "$os" in
    Linux)   platform=linux ;;
    Darwin)  platform=macos ;;
    FreeBSD) platform=freebsd ;;
    OpenBSD) platform=openbsd ;;
    NetBSD)  platform=netbsd ;;
    Haiku)   platform=haiku ;;
    *) die "unsupported OS '$os' — try 'pip install cvcpkg' instead" ;;
esac

# Same normalization as cvcpkg.platform.detect_arch; anything else is asked
# for verbatim, so a registry that publishes it still gets to answer.
arch="$(printf '%s' "$arch" | tr '[:upper:]' '[:lower:]')"
case "$arch" in
    x86_64|amd64)  arch=x86_64 ;;
    arm64|aarch64) arch=arm64 ;;
esac

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT INT TERM

# ---- Helpers --------------------------------------------------------------

# curl for registry URLs only, so CVCPKG_TOKEN never reaches another host.
# The header goes in a file, not argv, where `ps` would show it.
if [ -n "${CVCPKG_TOKEN:-}" ]; then
    (umask 077 && printf 'Authorization: Bearer %s\n' "$CVCPKG_TOKEN" >"$tmp/auth")
    registry_curl() { curl -H "@$tmp/auth" "$@"; }
else
    registry_curl() { curl "$@"; }
fi

# json_field FILE KEY: the string value of KEY in the JSON object in FILE.
# Splitting on , { } puts every key on its own line; a , inside a string can
# only start a fragment with text or a space, never with a bare "KEY":, so the
# first match is the top-level field.
json_field() {
    tr ',{}' '\n\n\n' <"$1" | sed -n "s/^ *\"$2\" *: *\"\\([^\"]*\\)\" *\$/\\1/p" | head -n 1
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    elif command -v sha256 >/dev/null 2>&1; then
        sha256 -q "$1"                                  # FreeBSD / OpenBSD base
    elif command -v openssl >/dev/null 2>&1; then
        openssl dgst -sha256 -r "$1" | awk '{print $1}'
    else
        die "no sha256sum, shasum, sha256 or openssl found — cannot verify the download"
    fi
}

# verify FILE EXPECTED_SHA256 WHAT
verify() {
    actual="$(sha256_of "$1")"
    expected="$(printf '%s' "$2" | tr '[:upper:]' '[:lower:]')"
    [ "$expected" = "$actual" ] \
        || die "checksum mismatch for $3 (expected $expected, got $actual)"
}

# unpack ARCHIVE DIR WHAT.  Sniffed rather than trusted to the name: registry
# bundles are all called .tar.zst whatever compression they actually hold.
unpack() {
    magic="$(od -An -tx1 -N6 "$1" | tr -d ' \n')"
    case "$magic" in
        1f8b*)        decompress=gzip ;;
        28b52ffd*)    decompress=zstd ;;
        fd377a585a00) decompress=xz ;;
        425a68*)      decompress=bzip2 ;;
        *) die "unrecognized archive format: $3" ;;
    esac
    command -v "$decompress" >/dev/null 2>&1 || die "$decompress is required to unpack $3"
    mkdir -p "$2"
    "$decompress" -dc "$1" | (cd "$2" && tar -xf -) || die "could not unpack $3"
}

# ---- Source 1: the cvcpkg registry ---------------------------------------
from_registry() {
    pkg_version="$(json_field "$tmp/latest.json" version)"
    sha256="$(json_field "$tmp/latest.json" sha256)"
    url="$(json_field "$tmp/latest.json" archive_url)"
    if [ -z "$url" ] || [ -z "$sha256" ]; then
        die "unexpected answer from $SERVER_URL: $(head -c 300 "$tmp/latest.json" 2>/dev/null)"
    fi
    case "$url" in /*) url="$SERVER_URL$url" ;; esac

    say "Installing cvcpkg $pkg_version for $platform/$arch from $SERVER_URL..."
    case "$url" in
        "$SERVER_URL"/*) registry_curl -fsSL -o "$tmp/bundle" "$url" ;;
        *)               curl -fsSL -o "$tmp/bundle" "$url" ;;
    esac || die "download failed: $url"
    verify "$tmp/bundle" "$sha256" "$url"
    unpack "$tmp/bundle" "$tmp/pkg" "$url"
    [ -f "$tmp/pkg/bin/cvcpkg" ] || die "the cvcpkg package at $url has no bin/cvcpkg"
    mv "$tmp/pkg/bin/cvcpkg" "$tmp/cvcpkg"
}

# ---- Source 2: the GitHub release (fallback) -----------------------------
from_github() {
    case "$platform-$arch" in
        linux-x86_64)   asset=cvcpkg-linux-x86_64 ;;
        macos-arm64)    asset=cvcpkg-macos-arm64 ;;
        freebsd-x86_64) asset=cvcpkg-freebsd-x86_64 ;;
        openbsd-x86_64) asset=cvcpkg-openbsd-x86_64 ;;
        netbsd-x86_64)  asset=cvcpkg-netbsd-x86_64 ;;
        haiku-x86_64)   asset=cvcpkg-haiku-x86_64 ;;
        *)
            die "no prebuilt cvcpkg for $platform/$arch on $SERVER_URL or in the $REPO releases — try 'pip install cvcpkg' instead"
            ;;
    esac

    if [ -n "$version" ]; then
        tag="cvcpkg-v${version%%+*}"
    else
        say "Resolving the latest cvcpkg release of $REPO..."
        tag="$(curl -fsSL "https://api.github.com/repos/${REPO}/releases" \
            | grep -o '"tag_name": *"cvcpkg-v[0-9][^"]*"' \
            | grep -v -- '-rc' \
            | head -n1 \
            | sed -E 's/.*"(cvcpkg-v[^"]+)".*/\1/')"
        [ -n "$tag" ] || die "could not resolve the latest cvcpkg release — set CVCPKG_VERSION=X.Y.Z"
    fi

    say "Installing cvcpkg ($tag) for $platform/$arch from github.com/$REPO..."
    base_url="https://github.com/${REPO}/releases/download/${tag}"
    curl -fsSL -o "$tmp/$asset" "${base_url}/${asset}" \
        || die "download failed: ${base_url}/${asset}"
    curl -fsSL -o "$tmp/$asset.sha256" "${base_url}/${asset}.sha256" \
        || die "checksum download failed: ${base_url}/${asset}.sha256"
    verify "$tmp/$asset" "$(awk '{print $1}' "$tmp/$asset.sha256")" "$asset"
    mv "$tmp/$asset" "$tmp/cvcpkg"
}

# ---- Pick the source ----------------------------------------------------
# Only "nothing for this platform / version" (404), or a server too old to
# have the lookup at all (405: the path matches its DELETE route), falls back
# to GitHub.  Any other failure stops here: installing upstream's build
# because this registry was briefly unreachable is exactly what a fork does
# not want.
say "Looking up cvcpkg${version:+ $version} for $platform/$arch on $SERVER_URL..."
status="$(registry_curl -sS -L -G -o "$tmp/latest.json" -w '%{http_code}' \
    --data-urlencode "platform=$platform" \
    --data-urlencode "arch=$arch" \
    --data-urlencode "version=$version" \
    "$SERVER_URL/v1/packages/cvcpkg/latest")" \
    || die "could not reach $SERVER_URL — pass --server (or set CVCPKG_SERVER_URL) to use another registry"

case "$status" in
    200)
        from_registry
        ;;
    404)
        say "No cvcpkg${version:+ $version} for $platform/$arch on $SERVER_URL; falling back to the GitHub releases of $REPO."
        from_github
        ;;
    405)
        say "$SERVER_URL is a cvcpkg server too old to publish cvcpkg itself; falling back to the GitHub releases of $REPO."
        from_github
        ;;
    401|403)
        die "$SERVER_URL requires authentication to read packages — set CVCPKG_TOKEN"
        ;;
    *)
        die "$SERVER_URL answered HTTP $status looking up cvcpkg: $(head -c 300 "$tmp/latest.json" 2>/dev/null)"
        ;;
esac

# ---- Install ------------------------------------------------------------
if ! mkdir -p "$INSTALL_DIR" 2>/dev/null || [ ! -w "$INSTALL_DIR" ]; then
    die "cannot write to $INSTALL_DIR — re-run with a writable --install-dir, or: curl -fsSL $DEFAULT_SERVER_URL/install.sh | sudo sh -s -- --install-dir $INSTALL_DIR"
fi
chmod +x "$tmp/cvcpkg"
mv "$tmp/cvcpkg" "$INSTALL_DIR/cvcpkg"

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
