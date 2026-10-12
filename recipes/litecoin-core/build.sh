#!/usr/bin/env bash
# recipes/litecoin-core/build.sh — stage the official Litecoin Core release
# binaries (Linux, macOS).
#
# Litecoin Core publishes Gitian builds per host triple.  We download the
# archive matching the build host, check it against the sha256 pinned below
# (copied from the release's clearsigned SHA256SUMS.asc, whose signature and
# Gitian assertions were verified when the version was pinned — see the
# recipe notes), and copy the node/CLI tools into the prefix.  Nothing is
# compiled.
#
# Not staged: litecoin-qt (the GUI) and test_litecoin.
#
# The release archives carry no licence files, sample config or rpcauth.py,
# so the source tarball (in the same signed SHA256SUMS) is fetched too and
# only those files are extracted from it.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
RECIPE_DIR="${CVC_RECIPE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

VER="0.21.5.8"
BASE="https://github.com/litecoin-project/litecoin/releases/download/v${VER}"

case "$(uname -s)" in
    Linux)
        case "$(uname -m)" in
            x86_64|amd64)  TRIPLE="x86_64-linux-gnu" ;;
            aarch64|arm64) TRIPLE="aarch64-linux-gnu" ;;
            *) echo "Unsupported Linux arch: $(uname -m)" >&2; exit 1 ;;
        esac ;;
    Darwin)
        # Upstream ships x86_64 macOS binaries only (Rosetta 2 on Apple
        # Silicon); the recipe declares macos-x86_64 alone.
        case "$(uname -m)" in
            x86_64)        TRIPLE="osx64" ;;
            *) echo "Unsupported macOS arch: $(uname -m) (upstream ships x86_64 only)" >&2; exit 1 ;;
        esac ;;
    *) echo "Unsupported host OS: $(uname -s)" >&2; exit 1 ;;
esac

# From the release's SHA256SUMS.asc (keep in sync with source.artifacts in
# recipe.yaml).
sha_for() {
    case "$1" in
        litecoin-0.21.5.8-x86_64-linux-gnu.tar.gz)  echo "43200c9f9d65ebc126ea5833ca9429e144c4b3273da6bb9f4e89fd7450ab1be9" ;;
        litecoin-0.21.5.8-aarch64-linux-gnu.tar.gz) echo "129f55defb9045d5635566382c7b9f6fd227d4106a6fc07847a38b25ff9b29a1" ;;
        litecoin-0.21.5.8-osx64.tar.gz)             echo "32fda6b90dba7103073486e63d01c39c7586ceabe0ce06672e756e3be957be3b" ;;
        litecoin-0.21.5.8.tar.gz)                   echo "5c38653b1d45fd2d3975634ce9d44161512ce7afa6586424f707bdead14a772b" ;;
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
BIN_TGZ="litecoin-${VER}-${TRIPLE}.tar.gz"
SRC_TGZ="litecoin-${VER}.tar.gz"
fetch "${BIN_TGZ}"
fetch "${SRC_TGZ}"

X="${CVC_BUILD_DIR}/extract"
rm -rf "${X}"
mkdir -p "${X}"
tar -xzf "${CVC_BUILD_DIR}/${BIN_TGZ}" -C "${X}"
S="${X}/litecoin-${VER}"
test -x "${S}/bin/litecoind" || { echo "unexpected archive layout" >&2; exit 1; }

I="${CVC_INSTALL_DIR}"
mkdir -p "${I}/bin" "${I}/share/man/man1" "${I}/etc/litecoin" \
         "${I}/share/litecoin-core/rpcauth" "${I}/share/licenses/litecoin-core"
TOOLS="litecoind litecoin-cli litecoin-tx litecoin-wallet"
for t in ${TOOLS}; do
    cp -p "${S}/bin/${t}" "${I}/bin/${t}"
    chmod 0755 "${I}/bin/${t}"
    cp -p "${S}/share/man/man1/${t}.1" "${I}/share/man/man1/${t}.1"
done
if [[ -f "${S}/README.md" ]]; then
    mkdir -p "${I}/share/doc/litecoin-core"
    cp -p "${S}/README.md" "${I}/share/doc/litecoin-core/README.md"
fi

# Sample config, rpcauth.py and licence texts from the source tarball.
P="litecoin-${VER}"
SRC_FILES="COPYING share/examples/litecoin.conf share/rpcauth/rpcauth.py
share/rpcauth/README.md src/crc32c/LICENSE src/crypto/ctaes/COPYING
src/crypto/scrypt.cpp src/leveldb/LICENSE src/libmw/LICENSE
src/libmw/deps/caches/LICENSE.md src/libmw/deps/ghc/LICENSE
src/secp256k1-zkp/COPYING src/univalue/COPYING"
members=""
for f in ${SRC_FILES}; do members="${members} ${P}/${f}"; done
# shellcheck disable=SC2086
tar -xzf "${CVC_BUILD_DIR}/${SRC_TGZ}" -C "${X}" ${members}
cp "${X}/${P}/share/examples/litecoin.conf" "${I}/etc/litecoin/litecoin.conf.sample"
cp "${X}/${P}/share/rpcauth/rpcauth.py" "${X}/${P}/share/rpcauth/README.md" \
   "${I}/share/litecoin-core/rpcauth/"
chmod 0755 "${I}/share/litecoin-core/rpcauth/rpcauth.py"
L="${I}/share/licenses/litecoin-core"
cp "${X}/${P}/COPYING"                           "${L}/COPYING"
cp "${X}/${P}/src/crc32c/LICENSE"                "${L}/crc32c-LICENSE"
cp "${X}/${P}/src/crypto/ctaes/COPYING"          "${L}/ctaes-COPYING"
cp "${X}/${P}/src/leveldb/LICENSE"               "${L}/leveldb-LICENSE"
cp "${X}/${P}/src/libmw/LICENSE"                 "${L}/libmw-LICENSE"
cp "${X}/${P}/src/libmw/deps/caches/LICENSE.md"  "${L}/caches-LICENSE.md"
cp "${X}/${P}/src/libmw/deps/ghc/LICENSE"        "${L}/ghc-filesystem-LICENSE"
cp "${X}/${P}/src/secp256k1-zkp/COPYING"         "${L}/secp256k1-zkp-COPYING"
cp "${X}/${P}/src/univalue/COPYING"              "${L}/univalue-COPYING"
# scrypt (BSD-2-Clause) carries its licence in the source header only.
sed -n '1,/\*\//p' "${X}/${P}/src/crypto/scrypt.cpp" > "${L}/scrypt-LICENSE"
grep -q "Colin Percival" "${L}/scrypt-LICENSE" || { echo "scrypt licence header not found" >&2; exit 1; }
cp "${RECIPE_DIR}/THIRD-PARTY-NOTICES.txt" "${L}/THIRD-PARTY-NOTICES.txt"

"${I}/bin/litecoind" -version > "${X}/version.txt"
head -n 1 "${X}/version.txt"
