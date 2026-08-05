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

./configure --prefix="${CVC_INSTALL_DIR}"

"${MAKE}" -j "${CVC_JOBS}"
"${MAKE}" install
