#!/usr/bin/env bash
# recipes/libvorbis/build.sh — build libvorbis from source using autotools.
#
# libogg must be available in CVC_DEPS_PREFIX before this recipe runs.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"

cd "${CVC_SOURCE_DIR}"

CONFIGURE_ARGS=(
    --prefix="${CVC_INSTALL_DIR}"
    --disable-dependency-tracking
    --with-ogg="${CVC_DEPS_PREFIX}"
)

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    CONFIGURE_ARGS+=(--disable-shared --enable-static)
else
    CONFIGURE_ARGS+=(--enable-shared --disable-static)
fi

# libvorbis 1.3.7's configure puts -force_cpusubtype_ALL into its darwin CFLAGS,
# and Apple's linker since Xcode 15 rejects it ("ld: unknown options:
# -force_cpusubtype_ALL"), so every macOS build failed.  The flag only appears in
# the darwin branch; drop it.
sed -i.orig 's/-force_cpusubtype_ALL//g' configure && rm -f configure.orig

# libvorbisfile calls rint()/rintf() but upstream links it without libm
# (libvorbisfile_la_LIBADD has only libvorbis and libogg).  Linux tolerates the
# undefined symbols; OpenBSD's ld (--no-allow-shlib-undefined) refused oggdec:
#   undefined reference due to --no-allow-shlib-undefined: rint
#   >>> referenced by .../lib/libvorbisfile.so.6.8
# The .la files used to hide this by adding -lm to every consumer's link.  Link
# libm into the libraries themselves.
export LIBS="${LIBS:+${LIBS} }-lm"
./configure "${CONFIGURE_ARGS[@]}"
make -j "${CVC_JOBS}"
make install

# Strip libtool archives (.la), as recipes/libx11 does: they record this job's
# scratch prefix (dependency_libs names libogg.la there), which is gone once
# cvcpkg relocates the bundle, and a downstream libtool link follows them:
# vorbis-tools on the BSDs failed with "libtool: error: cannot find the library
# '<libvorbis job prefix>/lib/libogg.la'".
# The shared library and the pkg-config .pc carry the real link information.
find "${CVC_INSTALL_DIR}" -name '*.la' -delete

cvc_rewrite_install_paths
