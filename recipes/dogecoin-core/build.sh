#!/usr/bin/env bash
# recipes/dogecoin-core/build.sh — stage the official Dogecoin Core release
# binaries (Linux).
#
# Dogecoin Core publishes Gitian builds per host triple.  We download the
# archive matching the build host, check it against the sha256 pinned below
# (copied from the release's clearsigned SHA256SUMS.asc, whose signature and
# Gitian assertions were verified when the version was pinned — see the
# recipe notes), and copy the node/CLI tools into the prefix.  Nothing is
# compiled.
#
# Not staged: dogecoin-qt (the GUI), test_dogecoin and libdogecoinconsensus.
#
# The release archives carry no licence files, and the release source
# tarball lacks the sample config, rpcuser.py and some licence texts, so
# those few files are fetched from the release commit on
# raw.githubusercontent.com, each pinned by sha256.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"
RECIPE_DIR="${CVC_RECIPE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"

VER="1.14.9"
BASE="https://github.com/dogecoin/dogecoin/releases/download/v${VER}"
# Tag v1.14.9; the commit the Gitian builders assert they built.
COMMIT="e0a1c157791544e818c901bd9341896965afbf9d"
RAW="https://raw.githubusercontent.com/dogecoin/dogecoin/${COMMIT}"

case "$(uname -s)" in
    Linux)
        case "$(uname -m)" in
            x86_64|amd64)  TRIPLE="x86_64-linux-gnu" ;;
            aarch64|arm64) TRIPLE="aarch64-linux-gnu" ;;
            *) echo "Unsupported Linux arch: $(uname -m)" >&2; exit 1 ;;
        esac ;;
    *) echo "Unsupported host OS: $(uname -s) (upstream ships no macOS daemon build)" >&2; exit 1 ;;
esac

# Release archives: from the release's SHA256SUMS.asc (keep in sync with
# source.artifacts in recipe.yaml).  Tree files: sha256 of the blob at
# ${COMMIT}.
sha_for() {
    case "$1" in
        dogecoin-1.14.9-x86_64-linux-gnu.tar.gz)  echo "4f227117b411a7c98622c970986e27bcfc3f547a72bef65e7d9e82989175d4f8" ;;
        dogecoin-1.14.9-aarch64-linux-gnu.tar.gz) echo "6928c895a20d0bcb6d5c7dcec753d35c884a471aaf8ad4242a89a96acb4f2985" ;;
        COPYING)                               echo "ff692211a86e69de91712125c9d6015fa5b1d15125329f80c09a9b61ec464bf6" ;;
        src/leveldb/LICENSE)                   echo "ccc19f1da0798ed666609b65a5b44dd8b3abe6fc08b9c0592eb76e82e174db19" ;;
        src/crypto/ctaes/COPYING)              echo "705391f5c3000c942b63c028977a372a5fb12eb0abc6210b3d835115e2544a06" ;;
        src/secp256k1/COPYING)                 echo "a735999c7e5649df6fcda6fb06ab97435851c392b1b93494ae8725f37441632f" ;;
        src/univalue/COPYING)                  echo "bb1d33d960a5ab422f23858845ec0ff28ff1a1f967643e74546eaf9b85acb3f3" ;;
        src/crypto/scrypt.cpp)                 echo "3ecc40e8726ae8406b105071024e65ae4a8314c3b3b52dcdeb955c131ca2e733" ;;
        contrib/debian/examples/dogecoin.conf) echo "591f0f724455c7398fcd36b9c50e1d0013dfffef1400c8bb954a46eca0818e9d" ;;
        share/rpcuser/rpcuser.py)              echo "ccf5eeb42690c67c3a1d3fd4159ecd88dd1fb4e6b24e48668f278975352aa763" ;;
        share/rpcuser/README.md)               echo "55468058e947c66aeee8390c6be78fe659b0e2e6c1649c77912950851e4eb237" ;;
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

# fetch <pin-key> <url> <dest>
fetch() {
    local key="$1" url="$2" dest="$3" expected actual
    expected="$(sha_for "${key}")"
    [[ -n "${expected}" ]] || { echo "No pinned sha256 for ${key}" >&2; exit 1; }
    if [[ ! -f "${dest}" ]]; then
        echo "Downloading ${url} ..."
        mkdir -p "$(dirname "${dest}")"
        curl -fsSL --retry 3 -o "${dest}.part" "${url}"
        mv "${dest}.part" "${dest}"
    fi
    actual="$(sha256_of "${dest}")"
    if [[ "${actual}" != "${expected}" ]]; then
        echo "sha256 mismatch for ${key}: got ${actual}, expected ${expected}" >&2
        exit 1
    fi
    echo "sha256 OK: ${key}"
}

mkdir -p "${CVC_BUILD_DIR}"
BIN_TGZ="dogecoin-${VER}-${TRIPLE}.tar.gz"
fetch "${BIN_TGZ}" "${BASE}/${BIN_TGZ}" "${CVC_BUILD_DIR}/${BIN_TGZ}"
TREE="${CVC_BUILD_DIR}/tree"
TREE_FILES="COPYING src/leveldb/LICENSE src/crypto/ctaes/COPYING
src/secp256k1/COPYING src/univalue/COPYING src/crypto/scrypt.cpp
contrib/debian/examples/dogecoin.conf share/rpcuser/rpcuser.py
share/rpcuser/README.md"
for f in ${TREE_FILES}; do
    fetch "${f}" "${RAW}/${f}" "${TREE}/${f}"
done

X="${CVC_BUILD_DIR}/extract"
rm -rf "${X}"
mkdir -p "${X}"
tar -xzf "${CVC_BUILD_DIR}/${BIN_TGZ}" -C "${X}"
S="${X}/dogecoin-${VER}"
test -x "${S}/bin/dogecoind" || { echo "unexpected archive layout" >&2; exit 1; }

I="${CVC_INSTALL_DIR}"
mkdir -p "${I}/bin" "${I}/share/man/man1" "${I}/etc/dogecoin" \
         "${I}/share/dogecoin-core/rpcuser" "${I}/share/licenses/dogecoin-core"
TOOLS="dogecoind dogecoin-cli dogecoin-tx"
for t in ${TOOLS}; do
    cp -p "${S}/bin/${t}" "${I}/bin/${t}"
    chmod 0755 "${I}/bin/${t}"
    cp -p "${S}/share/man/man1/${t}.1" "${I}/share/man/man1/${t}.1"
done

cp "${TREE}/contrib/debian/examples/dogecoin.conf" "${I}/etc/dogecoin/dogecoin.conf.sample"
cp "${TREE}/share/rpcuser/rpcuser.py" "${TREE}/share/rpcuser/README.md" \
   "${I}/share/dogecoin-core/rpcuser/"
chmod 0755 "${I}/share/dogecoin-core/rpcuser/rpcuser.py"
L="${I}/share/licenses/dogecoin-core"
cp "${TREE}/COPYING"                  "${L}/COPYING"
cp "${TREE}/src/leveldb/LICENSE"      "${L}/leveldb-LICENSE"
cp "${TREE}/src/crypto/ctaes/COPYING" "${L}/ctaes-COPYING"
cp "${TREE}/src/secp256k1/COPYING"    "${L}/secp256k1-COPYING"
cp "${TREE}/src/univalue/COPYING"     "${L}/univalue-COPYING"
# scrypt (BSD-2-Clause) carries its licence in the source header only.
sed -n '1,/\*\//p' "${TREE}/src/crypto/scrypt.cpp" > "${L}/scrypt-LICENSE"
grep -q "Colin Percival" "${L}/scrypt-LICENSE" || { echo "scrypt licence header not found" >&2; exit 1; }
cp "${RECIPE_DIR}/THIRD-PARTY-NOTICES.txt" "${L}/THIRD-PARTY-NOTICES.txt"

"${I}/bin/dogecoind" -version > "${X}/version.txt"
head -n 1 "${X}/version.txt"
