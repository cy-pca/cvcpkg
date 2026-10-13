#!/usr/bin/env bash
# recipes/gperf/build.sh — build GNU gperf from source on Linux, macOS, BSD.
#
# gperf is an autotools (C++) project, not CMake, so this sources the common
# env only for its variables — not for cvc_cmake_build.  Mirrors bison/build.sh,
# the other autotools host tool in this tree.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# BSD make cannot parse gperf's generated Makefiles; require GNU make there.
MAKE=make
case "$(uname -s)" in
    FreeBSD|OpenBSD|NetBSD|DragonFly)
        if command -v gmake >/dev/null 2>&1; then
            MAKE=gmake
        fi
        ;;
esac

cd "${CVC_SOURCE_DIR}"

# gperf's configure uses the old one-argument AC_INIT, so PACKAGE_TARNAME is
# empty and the default docdir is share/doc/ itself.  Name the package's own
# directory, so the manual installs as share/doc/gperf/gperf.html.
./configure --prefix="${CVC_INSTALL_DIR}" \
    --docdir="${CVC_INSTALL_DIR}/share/doc/gperf" \
    --htmldir="${CVC_INSTALL_DIR}/share/doc/gperf"

"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install

[ -f "${CVC_INSTALL_DIR}/share/doc/gperf/gperf.html" ] || {
    echo "gperf: manual not at share/doc/gperf/gperf.html" >&2; exit 1; }
