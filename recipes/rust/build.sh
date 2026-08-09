#!/usr/bin/env bash
# recipes/rust/build.sh — download and stage the official Rust toolchain.
#
# Rust ships prebuilt standalone installers per host triple. We download
# the one matching the build host, verify its published sha256, and run
# the bundled install.sh to stage rustc/cargo/std into CVC_INSTALL_DIR.
#
# This script covers Linux, macOS, FreeBSD and NetBSD. There is deliberately no
# OpenBSD branch: Rust has no official x86_64-unknown-openbsd release (tier-3,
# and absent from the channel manifest entirely), so the recipe declares no
# openbsd column and this script would have nothing to download. Same story for
# the aarch64 BSD triples.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

RUST_VER="1.90.0"
BASE="https://static.rust-lang.org/dist"

case "$(uname -m)" in
    x86_64|amd64)  ARCH="x86_64" ;;
    aarch64|arm64) ARCH="aarch64" ;;
    *) echo "Unsupported host arch: $(uname -m)" >&2; exit 1 ;;
esac
# The BSD triples are '<arch>-unknown-<os>' with no ABI field, unlike Linux's
# trailing '-gnu'. Only the combinations Rust actually publishes are reachable
# here; anything else falls into sha_for()'s empty default and aborts below.
case "$(uname -s)" in
    Linux)   TRIPLE="${ARCH}-unknown-linux-gnu" ;;
    Darwin)  TRIPLE="${ARCH}-apple-darwin" ;;
    FreeBSD) TRIPLE="${ARCH}-unknown-freebsd" ;;
    NetBSD)  TRIPLE="${ARCH}-unknown-netbsd" ;;
    *) echo "Unsupported host OS: $(uname -s)" >&2; exit 1 ;;
esac

# sha256 of the official release tarballs. Every value below was recomputed from
# the downloaded tarball and cross-checked against BOTH
# static.rust-lang.org/dist/<file>.sha256 and the `hash =` field of
# channel-rust-1.90.0.toml. aarch64-{freebsd,netbsd} and every openbsd triple are
# missing on purpose — Rust publishes no binary for them.
sha_for() {
    case "$1" in
        x86_64-unknown-linux-gnu)  echo "e453bae1c68d02fe2eae065c5452d5731308164cd154154c6ee442d2fa590685" ;;
        aarch64-unknown-linux-gnu) echo "293f412e3412c3aa3398c78ebbdf898fa08eacad80c85a7332ce1a455504c5fc" ;;
        x86_64-apple-darwin)       echo "3d1d24e1d4bedb421ca1a16060c21f4d803eaefba585c0b5b5d0b1e56692ef4b" ;;
        aarch64-apple-darwin)      echo "a11b52e34f5e80cb25d49f7943ae60e0b069b431727a4c09b2c890ceebee3687" ;;
        x86_64-unknown-freebsd)    echo "6c7ebf6acbe00873680a190152d47aeebe76e237195b974c593a67227123b2ef" ;;
        x86_64-unknown-netbsd)     echo "c573de875797c701ccf4829d67a563e389633a54d51683c715049a0de774b691" ;;
        *) echo "" ;;
    esac
}

# Echo the sha256 of $1 using whichever tool this host actually has. There is no
# portable one: GNU coreutils has sha256sum, macOS ships shasum(1), FreeBSD has
# sha256(1), and NetBSD reaches it only through cksum -a sha256. Probing beats
# assuming — the old `sha256sum || shasum` pair finds neither on NetBSD, which
# would have turned the new BSD columns into an unverified download.
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    elif command -v sha256 >/dev/null 2>&1; then
        sha256 -q "$1"
    elif command -v cksum >/dev/null 2>&1; then
        cksum -a sha256 -q < "$1" | awk '{print $1}'
    else
        echo "no sha256 tool found (tried sha256sum, shasum, sha256, cksum)" >&2
        return 1
    fi
}

TARBALL="rust-${RUST_VER}-${TRIPLE}.tar.gz"
ARCHIVE="${CVC_BUILD_DIR}/${TARBALL}"
EXPECTED="$(sha_for "${TRIPLE}")"
[ -n "${EXPECTED}" ] || {
    echo "No pinned sha256 for triple ${TRIPLE} — Rust publishes no official" >&2
    echo "binary release for it (tier-3), so this host cannot build recipes/rust." >&2
    exit 1
}

echo "Downloading ${BASE}/${TARBALL}..."
curl -fSL -o "${ARCHIVE}" "${BASE}/${TARBALL}"

echo "Verifying sha256..."
ACTUAL="$(sha256_of "${ARCHIVE}")"
if [ "${ACTUAL}" != "${EXPECTED}" ]; then
    echo "sha256 mismatch for ${TARBALL}: expected ${EXPECTED}, got ${ACTUAL}" >&2
    exit 1
fi

echo "Extracting..."
tar xf "${ARCHIVE}" -C "${CVC_BUILD_DIR}"

INSTALLER="${CVC_BUILD_DIR}/rust-${RUST_VER}-${TRIPLE}/install.sh"
echo "Installing into ${CVC_INSTALL_DIR}..."
sh "${INSTALLER}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --without=rust-docs \
    --disable-ldconfig

# Prove the staged toolchain actually runs on this host before anything
# downstream tries to compile with it: a wrong-triple tarball or a missing
# runtime library surfaces here instead of three recipes later, inside a cargo
# invocation nested under pip. build.ps1 does the same for rustc.
"${CVC_INSTALL_DIR}/bin/rustc" --version
"${CVC_INSTALL_DIR}/bin/cargo" --version
