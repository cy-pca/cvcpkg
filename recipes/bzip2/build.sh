#!/usr/bin/env bash
# recipes/bzip2/build.sh — build bzip2 from source.
#
# Upstream 1.0.8 ships two Makefiles: `Makefile` (static lib + tools)
# and `Makefile-libbz2_so` (shared lib).  We build both so consumers
# can pick either link mode.  A pkg-config file is generated
# post-install (upstream doesn't provide one).
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

CFLAGS_ALL="${CFLAGS:-} -D_FILE_OFFSET_BITS=64"
# bzip2's build.sh is self-contained (doesn't source _common/env-*.sh), so the
# shared macOS clang-16+ legacy-C relaxation doesn't reach it. Add it here for
# macOS/Xcode 26.5 (clang 21), where implicit-function-declaration/implicit-int
# are errors by default. Harmless on other platforms' compilers.
if [ "$(uname -s)" = "Darwin" ]; then
    CFLAGS_ALL="${CFLAGS_ALL} -Wno-implicit-function-declaration -Wno-implicit-int -Wno-int-conversion"
fi
export CFLAGS="${CFLAGS_ALL}"

# BSD/macOS make sometimes needs GNU make; use gmake if present.
MAKE=make
if command -v gmake >/dev/null 2>&1; then
    MAKE=gmake
fi

$MAKE -f Makefile-libbz2_so -j "${CVC_JOBS}" CC="${CC:-cc}"
$MAKE clean
$MAKE -j "${CVC_JOBS}" CC="${CC:-cc}"

$MAKE install PREFIX="${CVC_INSTALL_DIR}"

# The Makefile-libbz2_so target produces libbz2.so.1.0.8 in the source
# dir; install and symlink it.
case "$(uname -s)" in
    Darwin)
        # Rebuild as a proper .dylib on macOS (Makefile-libbz2_so emits
        # ELF-style SONAMEs that don't work with dyld).
        clang -dynamiclib -install_name "@rpath/libbz2.1.0.dylib" \
            -compatibility_version 1.0 -current_version 1.0.8 \
            -o "${CVC_INSTALL_DIR}/lib/libbz2.1.0.8.dylib" \
            ${CFLAGS_ALL} blocksort.o huffman.o crctable.o randtable.o \
            compress.o decompress.o bzlib.o
        ln -sf libbz2.1.0.8.dylib "${CVC_INSTALL_DIR}/lib/libbz2.1.0.dylib"
        ln -sf libbz2.1.0.8.dylib "${CVC_INSTALL_DIR}/lib/libbz2.dylib"
        ;;
    *)
        mkdir -p "${CVC_INSTALL_DIR}/lib"
        cp libbz2.so.1.0.8 "${CVC_INSTALL_DIR}/lib/"
        ln -sf libbz2.so.1.0.8 "${CVC_INSTALL_DIR}/lib/libbz2.so.1.0"
        ln -sf libbz2.so.1.0.8 "${CVC_INSTALL_DIR}/lib/libbz2.so.1"
        ln -sf libbz2.so.1.0.8 "${CVC_INSTALL_DIR}/lib/libbz2.so"
        ;;
esac

# pkg-config file — upstream doesn't provide one.
mkdir -p "${CVC_INSTALL_DIR}/lib/pkgconfig"
cat > "${CVC_INSTALL_DIR}/lib/pkgconfig/bzip2.pc" <<PC
prefix=\${pcfiledir}/../..
exec_prefix=\${prefix}
libdir=\${exec_prefix}/lib
includedir=\${prefix}/include

Name: bzip2
Description: Burrows-Wheeler compression library
URL: https://sourceware.org/bzip2/
Version: 1.0.8
Libs: -L\${libdir} -lbz2
Cflags: -I\${includedir}
PC

if command -v cvc_rewrite_install_paths >/dev/null 2>&1; then
    cvc_rewrite_install_paths
fi
