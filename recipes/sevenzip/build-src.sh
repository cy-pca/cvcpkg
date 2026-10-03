#!/usr/bin/env bash
# recipes/sevenzip/build-src.sh — build the 7-Zip console (7zz) from source on
# the BSDs and Haiku, which have no official prebuilt binaries.
#
# cvcpkg has already fetched + extracted the source tarball into CVC_SOURCE_DIR
# (Python lzma), so this script needs no curl/xz/tar — only a C++ compiler and
# GNU make, which OpenBSD base lacks curl/xz for but the builders provide.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh" ]; then
  # shellcheck disable=SC1090
  . "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"
fi

# The GNU makefile needs GNU make; the BSDs' default make (bmake/pmake) chokes.
MAKE=make
command -v gmake >/dev/null 2>&1 && MAKE=gmake

# 7zip_gcc.mak links `-lpthread -ldl`, but OpenBSD and Haiku have no libdl
# (dlopen lives in libc), so drop -ldl there via the LIB2 override.
LIB2_OVERRIDE=()
case "${CVC_PLATFORM}" in
  openbsd|haiku) LIB2_OVERRIDE=(LIB2=-lpthread) ;;
esac

cd "${CVC_SOURCE_DIR}/CPP/7zip/Bundles/Alone2"
"$MAKE" -j"${CVC_JOBS:-2}" -f makefile.gcc "${LIB2_OVERRIDE[@]}"

bin="$(find . -name '7zz' -type f 2>/dev/null | head -1)"
if [ -z "$bin" ]; then echo "7zz binary not produced" >&2; exit 1; fi

mkdir -p "${CVC_INSTALL_DIR}/bin"
cp "$bin" "${CVC_INSTALL_DIR}/bin/7zz"
chmod +x "${CVC_INSTALL_DIR}/bin/7zz"
ln -sf 7zz "${CVC_INSTALL_DIR}/bin/7z"

echo "7-Zip built from source:"
"${CVC_INSTALL_DIR}/bin/7zz" i | head -1 || true
