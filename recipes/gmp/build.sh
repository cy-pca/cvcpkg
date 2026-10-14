#!/usr/bin/env bash
# recipes/gmp/build.sh — build GMP from source on Linux and macOS.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=recipes/_common/env-linux.sh
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"

shared_flags=()
if [[ "${CVC_LINK}" == "static" ]]; then
    shared_flags+=(--disable-shared --enable-static)
else
    shared_flags+=(--enable-shared --enable-static)
fi

# x86/x86_64: build a FAT binary. Without it GMP's config.guess names the BUILD
# machine's exact CPU (haswell, skylake, zen...) and configure both selects that
# CPU's mpn assembly and adds -march/-mtune=<cpu> to CFLAGS (recorded in gmp.h
# as __GMP_CFLAGS, which MPFR copies when its own CFLAGS is unset). The bundle
# then carries BMI2/ADX (mulx, adcx/adox) or AVX-512 code and dies with SIGILL
# on older x86_64: seen 2026-10-14 when gcc's cc1plus loaded this libgmp on a
# Sandy Bridge Xeon (E5-2640). --enable-fat builds every x86_64 kernel family,
# picks one by CPUID at first use (mpn/x86_64/fat/fat.c), and clears the
# CPU-specific -march/-mtune (configure.ac: "If the user asked for a fat build,
# override the path and flags"), which is what Debian and Fedora ship. It is a
# no-op on other architectures (fat_path is x86-only), but say so explicitly.
fat_flags=()
case "$(uname -m)" in
    x86_64|amd64|i?86|i86pc|x86) fat_flags+=(--enable-fat) ;;
esac

# --with-pic: PIC objects in libgmp.a too. GMP otherwise assembles its static
# objects non-PIC (absolute references to tables such as __gmp_binvert_limb_table),
# so the archive cannot be linked into a PIE executable or a shared library,
# which is what every current toolchain produces by default. macOS ld rejects it
# outright ("Illegal text-relocations", seen 2026-10-14 when test.sh linked
# libgmp.a). The shared library was always PIC; this makes the static one usable.
#
# fat_flags is empty off x86, and macOS's bash 3.2 calls "${fat_flags[@]}" on an
# empty array an unbound variable under `set -u` (seen 2026-10-14 on macos-latest,
# arm64). The ${a[@]+"${a[@]}"} form expands to nothing there instead.
./configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --enable-cxx \
    --with-pic \
    ${fat_flags[@]+"${fat_flags[@]}"} \
    "${shared_flags[@]}"

make -j "${CVC_JOBS}"
make install

# Ensure installed .pc/.cmake files are relocatable.
cvc_rewrite_install_paths
# GMP is autotools, so libtool bakes the temp $CVC_INSTALL_DIR into the dylib
# ids/deps (libgmp, libgmpxx). Rewrite them to @rpath so the relocated bundle
# loads on macOS (no-op on other platforms).
cvc_relocate_macos_dylibs
