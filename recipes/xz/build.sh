#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

# (no-op touch: re-push to embed the current recipes/_common/env-openbsd.sh,
#  which now exports LD_LIBRARY_PATH so cmake, invoked here as a build-tool
#  dependency, can resolve its own runtime deps like libcurl.so on OpenBSD.)

# On OpenBSD, xz's tuklib_cpucores.c includes <sys/sysctl.h> without
# first pulling in <sys/types.h>, so u_int32_t / u_int64_t are undefined.
# Force-include <sys/types.h> to work around it.
if [[ "${CVC_PLATFORM}" == "openbsd" ]]; then
    export CFLAGS="${CFLAGS:-} -include sys/types.h"
fi

# FreeBSD/OpenBSD: no NLS.  Their libc has no gettext, so xz's
# find_package(Intl) took whatever libintl CMake found, and +cvc.5's xz and
# lzmainfo NEEDed an undeclared one: the builder's ports libintl.so.8.1 on
# OpenBSD, libintl.so.8 (ports' or cvcpkg gettext's) on FreeBSD.  Only those
# two tools use it (translated messages); liblzma never links libintl.
_extra=()
case "${CVC_PLATFORM}" in
    freebsd|openbsd) _extra+=(-DENABLE_NLS=OFF) ;;
esac

cvc_cmake_build \
    ${_extra[@]+"${_extra[@]}"} \
    -DBUILD_TESTING=OFF
