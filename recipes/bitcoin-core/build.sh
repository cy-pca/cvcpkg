#!/usr/bin/env bash
# recipes/bitcoin-core/build.sh — stage the official Bitcoin Core release
# binaries (Linux, macOS).
#
# Bitcoin Core publishes reproducible (Guix) builds per host triple.  We
# download the archive matching the build host, check it against the sha256
# pinned below (copied from the release's SHA256SUMS, whose guix-builder PGP
# signatures were verified when the version was pinned — see the recipe
# notes), and copy the node/CLI tools into the prefix.  Nothing is compiled.
#
# Not staged: bitcoin-qt / libexec/bitcoin-gui (the GUI), libexec/bitcoin-node
# (the experimental multiprocess node) and libexec/test_bitcoin.
#
# The release archives carry no licence files, so the source tarball (in the
# same signed SHA256SUMS) is fetched too and only its COPYING files are
# extracted into share/licenses/bitcoin-core/.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

VER="31.1"
BASE="https://bitcoincore.org/bin/bitcoin-core-${VER}"

case "$(uname -s)" in
    Linux)
        case "$(uname -m)" in
            x86_64|amd64)  TRIPLE="x86_64-linux-gnu" ;;
            aarch64|arm64) TRIPLE="aarch64-linux-gnu" ;;
            *) echo "Unsupported Linux arch: $(uname -m)" >&2; exit 1 ;;
        esac ;;
    Darwin)
        case "$(uname -m)" in
            x86_64)        TRIPLE="x86_64-apple-darwin" ;;
            arm64|aarch64) TRIPLE="arm64-apple-darwin" ;;
            *) echo "Unsupported macOS arch: $(uname -m)" >&2; exit 1 ;;
        esac ;;
    *) echo "Unsupported host OS: $(uname -s)" >&2; exit 1 ;;
esac

# From https://bitcoincore.org/bin/bitcoin-core-31.1/SHA256SUMS (keep in sync
# with source.artifacts in recipe.yaml).
sha_for() {
    case "$1" in
        bitcoin-31.1-x86_64-linux-gnu.tar.gz)    echo "b80d9c3e04da78fb6f0569685673418cf686fadba9042d926d13fb87ff503f9e" ;;
        bitcoin-31.1-aarch64-linux-gnu.tar.gz)   echo "dcf1873f2208ba4f962f3398d47e154c39c0084be8f4553e05c940d0ace3d004" ;;
        bitcoin-31.1-x86_64-apple-darwin.tar.gz) echo "bc506958d0f387c1ea770bdc7c7192a505fa645ff62cabcc7761fa7eb89e867e" ;;
        bitcoin-31.1-arm64-apple-darwin.tar.gz)  echo "16a097c09fbd7eb78b240ce1dae123663ea2e5e377cfd6a951e71e227e23cf2f" ;;
        bitcoin-31.1.tar.gz)                     echo "50411d5b43c7e4c90099394759eb6c2add6e7c2dbe728840893d638b6fc6afc9" ;;
        *) echo "" ;;
    esac
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    elif command -v sha256 >/dev/null 2>&1; then
        sha256 -q "$1"
    else
        echo "no sha256 tool found (tried sha256sum, shasum, sha256)" >&2
        return 1
    fi
}

fetch() {
    local name="$1" expected
    expected="$(sha_for "${name}")"
    [[ -n "${expected}" ]] || { echo "No pinned sha256 for ${name}" >&2; exit 1; }
    if [[ ! -f "${CVC_BUILD_DIR}/${name}" ]]; then
        echo "Downloading ${BASE}/${name} ..."
        curl -fsSL --retry 3 -o "${CVC_BUILD_DIR}/${name}.part" "${BASE}/${name}"
        mv "${CVC_BUILD_DIR}/${name}.part" "${CVC_BUILD_DIR}/${name}"
    fi
    local actual
    actual="$(sha256_of "${CVC_BUILD_DIR}/${name}")"
    if [[ "${actual}" != "${expected}" ]]; then
        echo "sha256 mismatch for ${name}: got ${actual}, expected ${expected}" >&2
        exit 1
    fi
    echo "sha256 OK: ${name}"
}

mkdir -p "${CVC_BUILD_DIR}"
BIN_TGZ="bitcoin-${VER}-${TRIPLE}.tar.gz"
SRC_TGZ="bitcoin-${VER}.tar.gz"
fetch "${BIN_TGZ}"
fetch "${SRC_TGZ}"

X="${CVC_BUILD_DIR}/extract"
rm -rf "${X}"
mkdir -p "${X}"
tar -xzf "${CVC_BUILD_DIR}/${BIN_TGZ}" -C "${X}"
S="${X}/bitcoin-${VER}"
test -x "${S}/bin/bitcoind" || { echo "unexpected archive layout" >&2; exit 1; }

I="${CVC_INSTALL_DIR}"
mkdir -p "${I}/bin" "${I}/share/man/man1" "${I}/etc/bitcoin" \
         "${I}/share/bitcoin-core/rpcauth" "${I}/share/licenses/bitcoin-core"
TOOLS="bitcoind bitcoin-cli bitcoin-tx bitcoin-util bitcoin-wallet bitcoin"
for t in ${TOOLS}; do
    cp -p "${S}/bin/${t}" "${I}/bin/${t}"
    chmod 0755 "${I}/bin/${t}"
    if [[ -f "${S}/share/man/man1/${t}.1" ]]; then
        cp -p "${S}/share/man/man1/${t}.1" "${I}/share/man/man1/${t}.1"
    fi
done
cp -p "${S}/bitcoin.conf" "${I}/etc/bitcoin/bitcoin.conf.sample"
cp -p "${S}/share/rpcauth/rpcauth.py" "${S}/share/rpcauth/README.md" \
      "${I}/share/bitcoin-core/rpcauth/"
if [[ -f "${S}/README.md" ]]; then
    mkdir -p "${I}/share/doc/bitcoin-core"
    cp -p "${S}/README.md" "${I}/share/doc/bitcoin-core/README.md"
fi

# Licence texts for the shipped binaries' in-tree components.
SRC_LICENCES="COPYING src/crc32c/LICENSE src/crypto/ctaes/COPYING src/leveldb/LICENSE
src/minisketch/LICENSE src/secp256k1/COPYING"
members=""
for f in ${SRC_LICENCES}; do members="${members} bitcoin-${VER}/${f}"; done
# shellcheck disable=SC2086
tar -xzf "${CVC_BUILD_DIR}/${SRC_TGZ}" -C "${X}" ${members}
L="${I}/share/licenses/bitcoin-core"
cp "${X}/bitcoin-${VER}/COPYING"                   "${L}/COPYING"
cp "${X}/bitcoin-${VER}/src/crc32c/LICENSE"        "${L}/crc32c-LICENSE"
cp "${X}/bitcoin-${VER}/src/crypto/ctaes/COPYING"  "${L}/ctaes-COPYING"
cp "${X}/bitcoin-${VER}/src/leveldb/LICENSE"       "${L}/leveldb-LICENSE"
cp "${X}/bitcoin-${VER}/src/minisketch/LICENSE"    "${L}/minisketch-LICENSE"
cp "${X}/bitcoin-${VER}/src/secp256k1/COPYING"     "${L}/secp256k1-COPYING"

"${I}/bin/bitcoind" -version > "${X}/version.txt"
head -n 1 "${X}/version.txt"
