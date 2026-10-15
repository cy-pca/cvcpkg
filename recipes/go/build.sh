#!/usr/bin/env bash
# recipes/go/build.sh — download and stage the official Go distribution.
#
# go.dev publishes a prebuilt, relocatable GOROOT per OS/arch. We download the
# one matching the build host, verify its pinned sha256, stage it whole under
# lib/go, link bin/go + bin/gofmt into it, and pin GOTOOLCHAIN=local in its
# go.env. Nothing is compiled.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

GO_VER="1.27.2"
BASE="https://dl.google.com/go"

case "$(uname -m)" in
    x86_64|amd64)  ARCH="amd64" ;;
    aarch64|arm64) ARCH="arm64" ;;
    *) echo "Unsupported host arch: $(uname -m)" >&2; exit 1 ;;
esac
case "$(uname -s)" in
    Linux)   OS="linux" ;;
    Darwin)  OS="darwin" ;;
    FreeBSD) OS="freebsd" ;;
    *) echo "Unsupported host OS: $(uname -s)" >&2; exit 1 ;;
esac

# From https://go.dev/dl/?mode=json for go1.27.2. Keep in step with
# recipe.yaml's source.artifacts and build.ps1's $expected.
sha_for() {
    case "$1" in
        linux-amd64)   echo "ecbadb99091a3f46e31f5f934b068b1864eafa7995211b39eaddf76996045fe5" ;;
        linux-arm64)   echo "94f3e30b8e374bc285e7dadc11e0865726b9bc6e85b841ccceaabc0214c6b7c8" ;;
        darwin-amd64)  echo "587b59182488b23aa6e5fc25110405a3e0e5b38ed2f5b2f46ed13c32aee356fe" ;;
        darwin-arm64)  echo "76812b213b1b2302c978d28fa52fa92d541704b9e7d9d5db8002c50e4018c4c5" ;;
        freebsd-amd64) echo "3baf61eec549b0c3f638d82636388e36abf94aa14814a011f011d2648ad1eded" ;;
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

TARBALL="go${GO_VER}.${OS}-${ARCH}.tar.gz"
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

# The tarball's single top-level directory is go/; extract straight into the
# prefix's lib/ so it lands as lib/go without a second copy of ~250 MB.
mkdir -p "${CVC_INSTALL_DIR}/lib" "${CVC_INSTALL_DIR}/bin"
rm -rf "${CVC_INSTALL_DIR}/lib/go"
tar xzf "${ARCHIVE}" -C "${CVC_INSTALL_DIR}/lib"
rm -f "${ARCHIVE}"
GOROOT_DIR="${CVC_INSTALL_DIR}/lib/go"
[ -x "${GOROOT_DIR}/bin/go" ] || { echo "no bin/go in the distribution" >&2; exit 1; }

# A pinned toolchain must not fetch another one behind the user's back.
grep -q '^GOTOOLCHAIN=auto$' "${GOROOT_DIR}/go.env" \
    || { echo "unexpected go.env (no GOTOOLCHAIN=auto line)" >&2; exit 1; }
sed 's/^GOTOOLCHAIN=auto$/GOTOOLCHAIN=local/' "${GOROOT_DIR}/go.env" > "${GOROOT_DIR}/go.env.new"
cat > "${GOROOT_DIR}/go.env" <<'EOF'
# cvcpkg: GOTOOLCHAIN is pinned to `local` (upstream: auto), so a go.mod asking
# for a newer Go fails instead of downloading one. Export GOTOOLCHAIN=auto to
# restore upstream behaviour.
EOF
cat "${GOROOT_DIR}/go.env.new" >> "${GOROOT_DIR}/go.env"
rm -f "${GOROOT_DIR}/go.env.new"

# Relative symlinks: `go` derives GOROOT from its resolved executable path.
for tool in go gofmt; do
    ln -sfn "../lib/go/bin/${tool}" "${CVC_INSTALL_DIR}/bin/${tool}"
done

# Prove the staged toolchain runs on this host and sees the pinned settings.
env -u GOROOT -u GOTOOLCHAIN GOENV=off "${CVC_INSTALL_DIR}/bin/go" version
env -u GOROOT -u GOTOOLCHAIN GOENV=off "${CVC_INSTALL_DIR}/bin/go" env GOROOT GOTOOLCHAIN
