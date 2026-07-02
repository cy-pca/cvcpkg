#!/usr/bin/env bash
# recipes/f2c/build.sh — build the netlib f2c Fortran-77-to-C translator
# on Unix-like platforms.  The upstream distribution is a hand-written
# Makefile (`makefile.u`) which builds a single executable.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

cd "${CVC_SOURCE_DIR}"
cp -f makefile.u makefile

# gcc 14+ (default -std=gnu23) turns pre-C23 empty parameter lists into
# (void), which is fine for f2c's own definitions (none of them are
# called with arguments), but keep the build predictable by pinning to
# gnu17.  Add -DSkip_f2c_Undefs so `f2c.h` doesn't try to redefine
# standard names.
export CFLAGS="${CFLAGS:--O2} -std=gnu17 -DSkip_f2c_Undefs"

# makefile.u's `all:` target runs an `xsum` self-check which we skip —
# the `f2c` target is what we actually want.
make -j "${CVC_JOBS}" CC="${CC}" CFLAGS="${CFLAGS}" f2c

install -d "${CVC_INSTALL_DIR}/bin" \
           "${CVC_INSTALL_DIR}/include" \
           "${CVC_INSTALL_DIR}/share/man/man1"
install -m 755 f2c   "${CVC_INSTALL_DIR}/bin/f2c"
install -m 644 f2c.h "${CVC_INSTALL_DIR}/include/f2c.h"
install -m 644 f2c.1 "${CVC_INSTALL_DIR}/share/man/man1/f2c.1"
