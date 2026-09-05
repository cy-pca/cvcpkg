#!/usr/bin/env bash
# recipes/automake/build.sh — build GNU Automake from source.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 4)}"

# Prefer the autoconf/autom4te cvcpkg installed into this build's closure over
# anything on the host. That bundle is now relocatable (see recipes/autoconf),
# and using it avoids the OpenBSD/NetBSD trap where the ports `autoconf` is a
# version-dispatch wrapper that aborts unless AUTOCONF_VERSION is exported —
# which made automake's configure report "Autoconf 2.65 or better is required".
for _root in "${CVC_BUILD_PREFIX:-}" "${CVC_DEPS_PREFIX:-}"; do
    [[ -n "${_root}" ]] || continue
    [[ -z "${AUTOCONF:-}" && -x "${_root}/bin/autoconf" ]] && export AUTOCONF="${_root}/bin/autoconf"
    [[ -z "${AUTOM4TE:-}" && -x "${_root}/bin/autom4te" ]] && export AUTOM4TE="${_root}/bin/autom4te"
done

# GNU make on BSD; and, as a last resort where the closure lacks autoconf, fall
# back to the ports-versioned autotools names.
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        if command -v gmake >/dev/null 2>&1; then
            MAKE=gmake
        fi
        if [[ -z "${AUTOCONF:-}" ]]; then
            for candidate in autoconf-2.72 autoconf-2.71 autoconf-2.69 autoconf; do
                if command -v "${candidate}" >/dev/null 2>&1; then
                    export AUTOCONF="${candidate}"
                    break
                fi
            done
        fi
        if [[ -z "${AUTOM4TE:-}" ]]; then
            for candidate in autom4te-2.72 autom4te-2.71 autom4te-2.69 autom4te; do
                if command -v "${candidate}" >/dev/null 2>&1; then
                    export AUTOM4TE="${candidate}"
                    break
                fi
            done
        fi
        ;;
esac

cd "${CVC_SOURCE_DIR}"

./configure \
    --prefix="${CVC_INSTALL_DIR}"

"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install
