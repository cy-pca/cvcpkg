#!/usr/bin/env bash
# recipes/rust/build.sh — download and stage the official Rust toolchain.
#
# Rust ships prebuilt standalone installers per host triple. We download
# the one matching the build host, verify its published sha256, and run
# the bundled install.sh to stage rustc/cargo/std into CVC_INSTALL_DIR.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

RUST_VER="1.84.0"
BASE="https://static.rust-lang.org/dist"

case "$(uname -m)" in
    x86_64|amd64)  ARCH="x86_64" ;;
    aarch64|arm64) ARCH="aarch64" ;;
    *) echo "Unsupported host arch: $(uname -m)" >&2; exit 1 ;;
esac
case "$(uname -s)" in
    Linux)  TRIPLE="${ARCH}-unknown-linux-gnu" ;;
    Darwin) TRIPLE="${ARCH}-apple-darwin" ;;
    *) echo "Unsupported host OS: $(uname -s)" >&2; exit 1 ;;
esac

# sha256 of the official release tarballs (static.rust-lang.org/dist/*.sha256).
sha_for() {
    case "$1" in
        x86_64-unknown-linux-gnu)  echo "de2b041a6e62ec2c37c517eb58518f68fde5fc2f076218393ae06145d92a5682" ;;
        aarch64-unknown-linux-gnu) echo "282d281cb389bdc2c0671c2a74eeda46e010a158810d2137c3a948ae6c713543" ;;
        x86_64-apple-darwin)       echo "eafe087277ad8d7473f978d0779b4504d5b8064a781784aebd3e33c2541a13ce" ;;
        aarch64-apple-darwin)      echo "506dfc14115d2efa96fad9fa542d67027525aa46882a8e1ffb41e891737b689b" ;;
        *) echo "" ;;
    esac
}

TARBALL="rust-${RUST_VER}-${TRIPLE}.tar.gz"
ARCHIVE="${CVC_BUILD_DIR}/${TARBALL}"
EXPECTED="$(sha_for "${TRIPLE}")"
[ -n "${EXPECTED}" ] || { echo "No pinned sha256 for triple ${TRIPLE}" >&2; exit 1; }

echo "Downloading ${BASE}/${TARBALL}..."
curl -fSL -o "${ARCHIVE}" "${BASE}/${TARBALL}"

echo "Verifying sha256..."
if command -v sha256sum >/dev/null 2>&1; then
    echo "${EXPECTED}  ${ARCHIVE}" | sha256sum -c -
else
    echo "${EXPECTED}  ${ARCHIVE}" | shasum -a 256 -c -
fi

echo "Extracting..."
tar xf "${ARCHIVE}" -C "${CVC_BUILD_DIR}"

INSTALLER="${CVC_BUILD_DIR}/rust-${RUST_VER}-${TRIPLE}/install.sh"
echo "Installing into ${CVC_INSTALL_DIR}..."
sh "${INSTALLER}" \
    --prefix="${CVC_INSTALL_DIR}" \
    --without=rust-docs \
    --disable-ldconfig
