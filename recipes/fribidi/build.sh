#!/usr/bin/env bash
# recipes/fribidi/build.sh — build GNU FriBidi with Meson.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"

_default_lib=shared
if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    _default_lib=static
fi

# $ORIGIN for the library, $ORIGIN/../lib for bin/fribidi, which links it.  Set
# at link time, as recipes/wayland does: cvcpkg's packager appends the bin/
# entry itself only on linux, so bin/fribidi on FreeBSD and NetBSD could not
# find lib/libfribidi.so.0.  On macOS the packager rewrites meson's absolute
# install names to @rpath and adds @loader_path/../lib to bin/fribidi.
if [[ "${CVC_PLATFORM}" == "macos" ]]; then
    _rpath_flags="-Wl,-rpath,@loader_path"
else
    _rpath_flags="-Wl,-rpath,\$ORIGIN:\$ORIGIN/../lib"
fi

cd "${CVC_SOURCE_DIR}"

meson setup "${CVC_BUILD_DIR}" \
    --wrap-mode=nofallback \
    --prefix="${CVC_INSTALL_DIR}" \
    --buildtype=release \
    --libdir=lib \
    --default-library="${_default_lib}" \
    -Dc_link_args="${_rpath_flags}" \
    -Dtests=false \
    -Ddocs=false

ninja -C "${CVC_BUILD_DIR}" -j "${CVC_JOBS}"
ninja -C "${CVC_BUILD_DIR}" install

cvc_rewrite_install_paths
