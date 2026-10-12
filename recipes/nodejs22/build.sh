#!/usr/bin/env bash
# recipes/nodejs22/build.sh — download and stage the official Node.js 22 binaries.
#
# Node ships prebuilt, relocatable release tarballs per OS/arch. We download
# the one matching the build host, verify the sha256 pinned from the release's
# signed SHASUMS256.txt (see recipe.yaml for the gpgv procedure), and copy
# upstream's bin/ include/ lib/ share/ into CVC_INSTALL_DIR. Nothing is
# compiled.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

NODE_VER="22.23.3"
BASE="https://nodejs.org/dist/v${NODE_VER}"

case "$(uname -m)" in
    x86_64|amd64)  ARCH="x64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *) echo "Unsupported host arch: $(uname -m)" >&2; exit 1 ;;
esac
case "$(uname -s)" in
    Linux)  OS="linux" ;;
    Darwin) OS="darwin" ;;
    *) echo "Unsupported host OS: $(uname -s)" >&2; exit 1 ;;
esac

# sha256 of the release tarballs, copied from the signature-verified
# SHASUMS256.txt of v22.23.3. Keep in step with recipe.yaml's source.artifacts
# and build.ps1's $expected.
sha_for() {
    case "$1" in
        linux-x64)    echo "1084aa36196bba4c3a5e69a1ee388a6e4ff729dad09445fbcd434b28fe3c24af" ;;
        linux-arm64)  echo "5ced2d48d1d7198739b7f86804de0171aefb6823b684b12341d3321afc3cb0b2" ;;
        darwin-x64)   echo "8a677b0219178efd6eb0e475457c4afb452b521a92f6e67845a73bd85727f2a8" ;;
        darwin-arm64) echo "23b25245dcfb9af7262f8ff142e9e2e0af025368117329e7a7458a51e5922f53" ;;
        *) echo "" ;;
    esac
}

sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{print $1}'
    else
        echo "no sha256 tool found (tried sha256sum, shasum)" >&2
        return 1
    fi
}

STEM="node-v${NODE_VER}-${OS}-${ARCH}"
TARBALL="${STEM}.tar.gz"
ARCHIVE="${CVC_BUILD_DIR}/${TARBALL}"
EXPECTED="$(sha_for "${OS}-${ARCH}")"
[ -n "${EXPECTED}" ] || { echo "No pinned sha256 for ${OS}-${ARCH}" >&2; exit 1; }

echo "Downloading ${BASE}/${TARBALL}..."
curl -fSL --retry 3 -o "${ARCHIVE}" "${BASE}/${TARBALL}"

ACTUAL="$(sha256_of "${ARCHIVE}")"
if [ "${ACTUAL}" != "${EXPECTED}" ]; then
    echo "sha256 mismatch for ${TARBALL}: expected ${EXPECTED}, got ${ACTUAL}" >&2
    exit 1
fi
echo "sha256 OK (${ACTUAL})"

tar xzf "${ARCHIVE}" -C "${CVC_BUILD_DIR}"
SRC="${CVC_BUILD_DIR}/${STEM}"

mkdir -p "${CVC_INSTALL_DIR}/bin" "${CVC_INSTALL_DIR}/lib" \
         "${CVC_INSTALL_DIR}/include" "${CVC_INSTALL_DIR}/share/doc/node"
cp -p "${SRC}/bin/node" "${CVC_INSTALL_DIR}/bin/node"
cp -pR "${SRC}/lib/node_modules" "${CVC_INSTALL_DIR}/lib/"
cp -pR "${SRC}/include/node" "${CVC_INSTALL_DIR}/include/"
cp -pR "${SRC}/share/." "${CVC_INSTALL_DIR}/share/"
cp -p "${SRC}/LICENSE" "${SRC}/README.md" "${SRC}/CHANGELOG.md" \
      "${CVC_INSTALL_DIR}/share/doc/node/"

# corepack downloads yarn/pnpm on demand and, once enabled, writes shims into
# the prefix -- not something a pinned toolchain should carry. Node 25+ stopped
# bundling it as well. See the recipe notes.
rm -rf "${CVC_INSTALL_DIR}/lib/node_modules/corepack"

# Upstream's bin/npm and bin/npx are relative symlinks into
# lib/node_modules/npm/bin/. They only work while they stay symlinks (a copied
# npm-cli.js requires ../lib/cli.js relative to bin/), and they run whatever
# `node` is first on PATH. Replace them with launchers that resolve their own
# location (following symlinks to themselves) and exec the node beside them.
write_launcher() {
    # $1 = launcher name, $2 = entry point relative to the prefix
    cat > "${CVC_INSTALL_DIR}/bin/$1" <<EOF
#!/bin/sh
# cvcpkg nodejs22 launcher: run $1 on the node that ships beside it.
self=\$0
while [ -h "\$self" ]; do
    link=\$(readlink "\$self")
    case \$link in
        /*) self=\$link ;;
        *) self=\$(dirname -- "\$self")/\$link ;;
    esac
done
here=\$(CDPATH= cd -- "\$(dirname -- "\$self")" && pwd)
exec "\$here/node" "\$here/../$2" "\$@"
EOF
    chmod 0755 "${CVC_INSTALL_DIR}/bin/$1"
}
write_launcher npm lib/node_modules/npm/bin/npm-cli.js
write_launcher npx lib/node_modules/npm/bin/npx-cli.js

# Prove the staged runtime starts on this host before anything downstream
# relies on it (a wrong-arch tarball or a too-old glibc fails here).
"${CVC_INSTALL_DIR}/bin/node" --version
"${CVC_INSTALL_DIR}/bin/npm" --version
