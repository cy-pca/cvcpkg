#!/usr/bin/env bash
# recipes/jack/build.sh — build JACK2 (libjack) with its bundled waf build tool.
#
# STATIC LINKING NOTE: jack2's only static switch (--static) is documented as
# "Windows only".  On Linux/FreeBSD waf produces SHARED libraries exclusively;
# there is no supported static libjack.  We therefore always build shared and,
# when CVC_LINK=static is requested, emit a warning and still ship the shared
# libraries (honouring the request as closely as jack2 allows).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/bin}:${PATH}"
export PKG_CONFIG_PATH="${CVC_DEPS_PREFIX}/lib/pkgconfig:${CVC_DEPS_PREFIX}/libdata/pkgconfig:${CVC_DEPS_PREFIX}/share/pkgconfig${CVC_BUILD_PREFIX:+:${CVC_BUILD_PREFIX}/lib/pkgconfig:${CVC_BUILD_PREFIX}/libdata/pkgconfig:${CVC_BUILD_PREFIX}/share/pkgconfig}${PKG_CONFIG_PATH:+:${PKG_CONFIG_PATH}}"
export CPPFLAGS="-I${CVC_DEPS_PREFIX}/include${CPPFLAGS:+ ${CPPFLAGS}}"
export LDFLAGS="-L${CVC_DEPS_PREFIX}/lib -Wl,-rpath,\$ORIGIN${LDFLAGS:+ ${LDFLAGS}}"

if [[ "${CVC_LINK:-shared}" == "static" ]]; then
    echo "jack: WARNING — jack2 has no static libjack on ${CVC_PLATFORM}; building SHARED libraries." >&2
fi

cd "${CVC_SOURCE_DIR}"

# The bundled waf (jack2 1.9.22) still does 'import imp', which was removed in
# Python 3.12.  Apply a minimal, behaviour-identical shim to waflib/Context.py
# only when the running interpreter lacks the 'imp' module — imp.new_module(x)
# is exactly types.ModuleType(x).  On Python < 3.12 the tree is left untouched.
if ! python3 -c 'import imp' >/dev/null 2>&1; then
    sed -i 's/^import os, re, imp, sys$/import os, re, sys/' waflib/Context.py
    sed -i 's/imp\.new_module(WSCRIPT_FILE)/__import__("types").ModuleType(WSCRIPT_FILE)/' waflib/Context.py
fi

# Driver backends.  ALSA is Linux-specific, so enable it explicitly on Linux
# (fail loudly if alsa-lib is missing).  Other platforms let waf auto-detect
# (FreeBSD falls back to the OSS backend).
_backend_opts=()
case "${CVC_PLATFORM}" in
    linux) _backend_opts+=( --alsa ) ;;
esac

python3 ./waf configure \
    --prefix="${CVC_INSTALL_DIR}" \
    --libdir="${CVC_INSTALL_DIR}/lib" \
    ${_backend_opts[@]+"${_backend_opts[@]}"}
python3 ./waf build -j "${CVC_JOBS}"
python3 ./waf install

# jack2 does not emit libtool archives, but strip any just in case they cannot
# survive prefix relocation.
find "${CVC_INSTALL_DIR}" -name '*.la' -delete

cvc_rewrite_install_paths
