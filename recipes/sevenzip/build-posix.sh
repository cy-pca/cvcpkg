#!/usr/bin/env bash
# recipes/sevenzip/build-posix.sh — stage the official 7-Zip console (7zz) on
# linux/macOS from the prebuilt release tarball.  No compilation.
set -euo pipefail

VER="26.03"
BASE="https://github.com/ip7z/7zip/releases/download/${VER}"

case "${CVC_PLATFORM:-$(uname -s)}" in
  linux|Linux)
    case "$(uname -m)" in
      x86_64|amd64)  file="7z2603-linux-x64.tar.xz";   sha="dc99eff5008f1ab79bd7084c68513701547a808a89502bf4133683535ab3c695" ;;
      aarch64|arm64) file="7z2603-linux-arm64.tar.xz"; sha="2389ba20e4d8295e8709c20b6263b69bd1ec4972fe38a04ad7a1badbf595b996" ;;
      *) echo "unsupported linux arch: $(uname -m)" >&2; exit 1 ;;
    esac ;;
  macos|Darwin)
    file="7z2603-mac.tar.xz"; sha="5ca87677072c59f5602e5c49baa27d4694bacd2259b4e507f0094249d4281480" ;;
  *) echo "build-posix.sh: unsupported platform ${CVC_PLATFORM:-$(uname -s)}" >&2; exit 1 ;;
esac

work="${CVC_BUILD_DIR:-$(pwd)}/7z"
mkdir -p "$work"; cd "$work"
echo "Downloading ${BASE}/${file}"
curl -fSL --retry 8 --retry-delay 5 -o pkg.tar.xz "${BASE}/${file}"

got="$(sha256sum pkg.tar.xz 2>/dev/null | awk '{print $1}' || shasum -a 256 pkg.tar.xz | awk '{print $1}')"
if [ "$got" != "$sha" ]; then echo "sha256 mismatch: $got != $sha" >&2; exit 1; fi
# Clear LD_LIBRARY_PATH so tar/xz use the system liblzma, not a frozen-cvcpkg
# bundle's older copy (which lacks newer XZ_* symbols).
env -u LD_LIBRARY_PATH tar xf pkg.tar.xz

mkdir -p "${CVC_INSTALL_DIR}/bin"
# Prefer the static build (7zzs) for hermeticity; macOS ships only 7zz.
if [ -f 7zzs ]; then cp 7zzs "${CVC_INSTALL_DIR}/bin/7zz"; else cp 7zz "${CVC_INSTALL_DIR}/bin/7zz"; fi
chmod +x "${CVC_INSTALL_DIR}/bin/7zz"
ln -sf 7zz "${CVC_INSTALL_DIR}/bin/7z"

echo "7-Zip staged:"
"${CVC_INSTALL_DIR}/bin/7zz" i | head -1
