#!/usr/bin/env bash
# recipes/sevenzip/build-src.sh — build the 7-Zip console (7zz) from source on
# the BSDs and Haiku, which have no official prebuilt binaries.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh" ]; then
  # shellcheck disable=SC1090
  . "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"
fi

VER="26.03"
url="https://github.com/ip7z/7zip/releases/download/${VER}/7z2603-src.tar.xz"
sha="9cbde5099c6deb73691b0579063da5827522ccbbcba3f0020fd04e8c8c16c0d4"

work="${CVC_BUILD_DIR:-$(pwd)}/7zsrc"
mkdir -p "$work"; cd "$work"
echo "Downloading $url"
curl -fSL --retry 8 --retry-delay 5 -o src.tar.xz "$url"
got="$(sha256sum src.tar.xz 2>/dev/null | awk '{print $1}' || shasum -a 256 src.tar.xz | awk '{print $1}')"
if [ "$got" != "$sha" ]; then echo "sha256 mismatch: $got != $sha" >&2; exit 1; fi
# Clear LD_LIBRARY_PATH so tar/xz use the system liblzma (see build-posix.sh).
env -u LD_LIBRARY_PATH tar xf src.tar.xz

# The GNU makefile needs GNU make; the BSDs' default make (bmake/pmake) chokes.
MAKE=make
command -v gmake >/dev/null 2>&1 && MAKE=gmake

cd CPP/7zip/Bundles/Alone2
"$MAKE" -j"${CVC_JOBS:-2}" -f makefile.gcc

bin="$(find . -name '7zz' -type f 2>/dev/null | head -1)"
if [ -z "$bin" ]; then echo "7zz binary not produced" >&2; exit 1; fi

mkdir -p "${CVC_INSTALL_DIR}/bin"
cp "$bin" "${CVC_INSTALL_DIR}/bin/7zz"
chmod +x "${CVC_INSTALL_DIR}/bin/7zz"
ln -sf 7zz "${CVC_INSTALL_DIR}/bin/7z"

echo "7-Zip built from source:"
"${CVC_INSTALL_DIR}/bin/7zz" i | head -1 || true
