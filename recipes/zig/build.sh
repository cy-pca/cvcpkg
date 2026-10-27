#!/usr/bin/env bash
# recipes/zig/build.sh — download and stage the official Zig release.
#
# ziglang.org publishes one archive per OS/arch: a directory holding the zig
# executable, lib/ (standard library, compiler-rt, libc and libc++ sources),
# doc/langref.html, LICENSE and README.md. We download the one matching the
# build host, verify its pinned sha256, and stage it as bin/zig + lib/zig/ +
# share/doc/zig/. Nothing is compiled. Windows uses build.ps1.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

ZIG_VER="0.15.2"
BASE="https://ziglang.org/download/${ZIG_VER}"

case "$(uname -m)" in
    x86_64|amd64)  ARCH="x86_64" ;;
    aarch64|arm64) ARCH="aarch64" ;;
    *) echo "Unsupported host arch: $(uname -m)" >&2; exit 1 ;;
esac
case "$(uname -s)" in
    Linux)   OS="linux" ;;
    Darwin)  OS="macos" ;;
    FreeBSD) OS="freebsd" ;;
    *) echo "Unsupported host OS: $(uname -s)" >&2; exit 1 ;;
esac

# From https://ziglang.org/download/index.json for 0.15.2 (each file's minisign
# signature checked, see recipe.yaml). Keep in step with recipe.yaml's
# source.artifacts and build.ps1's $expected.
sha_for() {
    case "$1" in
        x86_64-linux)   echo "02aa270f183da276e5b5920b1dac44a63f1a49e55050ebde3aecc9eb82f93239" ;;
        aarch64-linux)  echo "958ed7d1e00d0ea76590d27666efbf7a932281b3d7ba0c6b01b0ff26498f667f" ;;
        x86_64-macos)   echo "375b6909fc1495d16fc2c7db9538f707456bfc3373b14ee83fdd3e22b3d43f7f" ;;
        aarch64-macos)  echo "3cc2bab367e185cdfb27501c4b30b1b0653c28d9f73df8dc91488e66ece5fa6b" ;;
        x86_64-freebsd) echo "5509ff57cd3f219165caed0da10221739af82742b9edfcda3f7bfaf4da7212dd" ;;
        *) echo "" ;;
    esac
}

# Hash from stdin: with a FILE operand that contains a backslash (a native
# Windows path) coreutils escapes the output line with a leading '\'.
sha256_of() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum < "$1" | awk '{print $1}'
    elif command -v shasum >/dev/null 2>&1; then
        shasum -a 256 < "$1" | awk '{print $1}'
    elif command -v sha256 >/dev/null 2>&1; then
        sha256 -q < "$1"
    else
        echo "no sha256 tool found (tried sha256sum, shasum, sha256)" >&2
        return 1
    fi
}

DIST="zig-${ARCH}-${OS}-${ZIG_VER}"
TARBALL="${DIST}.tar.xz"
ARCHIVE="${CVC_BUILD_DIR}/${TARBALL}"
EXPECTED="$(sha_for "${ARCH}-${OS}")"
[ -n "${EXPECTED}" ] || { echo "No pinned sha256 for ${ARCH}-${OS}" >&2; exit 1; }

echo "Downloading ${BASE}/${TARBALL}..."
curl -fSL --retry 3 -o "${ARCHIVE}" "${BASE}/${TARBALL}"

ACTUAL="$(sha256_of "${ARCHIVE}")"
if [ "${ACTUAL}" != "${EXPECTED}" ]; then
    echo "sha256 mismatch for ${TARBALL}: expected ${EXPECTED}, got ${ACTUAL}" >&2
    exit 1
fi
echo "sha256 OK (${ACTUAL})"

tar xJf "${ARCHIVE}" -C "${CVC_BUILD_DIR}"
rm -f "${ARCHIVE}"
SRC="${CVC_BUILD_DIR}/${DIST}"
[ -x "${SRC}/zig" ] && [ -f "${SRC}/lib/std/std.zig" ] \
    || { echo "unexpected archive layout (no zig + lib/std/std.zig in ${DIST}/)" >&2; exit 1; }

# zig finds its lib directory by searching each parent of its own resolved
# path for lib/zig/std/std.zig, so bin/zig + lib/zig/ is a layout it knows.
mkdir -p "${CVC_INSTALL_DIR}/bin" "${CVC_INSTALL_DIR}/lib" "${CVC_INSTALL_DIR}/share/doc/zig"
rm -rf "${CVC_INSTALL_DIR}/lib/zig"
mv "${SRC}/lib" "${CVC_INSTALL_DIR}/lib/zig"
cp -p "${SRC}/zig" "${CVC_INSTALL_DIR}/bin/zig"
cp -p "${SRC}/LICENSE" "${SRC}/README.md" "${SRC}/doc/langref.html" "${CVC_INSTALL_DIR}/share/doc/zig/"
rm -rf "${SRC}"

# Prove the staged toolchain runs on this host and found its own lib/zig.
"${CVC_INSTALL_DIR}/bin/zig" version
env -u ZIG_LIB_DIR "${CVC_INSTALL_DIR}/bin/zig" env
