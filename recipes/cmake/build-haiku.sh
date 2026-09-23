#!/usr/bin/env bash
# recipes/cmake/build-haiku.sh — bootstrap CMake from source ON a Haiku box.
#
# Runs on the Haiku target itself (delegated over SSH by cvcpkg.haikuhost), so
# there is NO pre-existing cvcpkg cmake/ninja to lean on — cmake bootstraps with
# the base image's gcc + make + python. Haiku is not a vanilla-cmake platform:
# two of cmake's bundled deps need porting, exactly what HaikuPorts patches.
#   1. libuv: cmake bundles it but Utilities/cmlibuv/CMakeLists.txt has no Haiku
#      branch, so the Haiku platform sources (haiku.c, posix-poll.c, …) are never
#      compiled and every uv__* platform symbol is undefined at link. Add the
#      branch (the sources ship in the tarball) linking `network` + `bsd`.
#   2. arc4random_buf: Haiku declares it (stdlib.h) but only provides it in
#      libbsd, so libarchive's static fallback both conflicts with the header and
#      fails to link. Force the have-it path and link `-lbsd`.
# Sockets live in libnetwork on Haiku, so the final link also needs `-lnetwork`
# (libuv's tcp.c). Verified from source on the cluster's haiku-build VM.
set -euo pipefail
: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_SOURCE_DIR:?CVC_SOURCE_DIR must be set}"
: "${CVC_JOBS:=$(nproc 2>/dev/null || echo 4)}"

cd "${CVC_SOURCE_DIR}"

# ── 1. libuv: add a Haiku platform block to the bundled CMakeLists ──────────
python3 - <<'PY'
import os
p = "Utilities/cmlibuv/CMakeLists.txt"
s = open(p).read()
if 'STREQUAL "Haiku"' not in s:
    block = (
        'if(CMAKE_SYSTEM_NAME STREQUAL "Haiku")\n'
        '  list(APPEND uv_defines _BSD_SOURCE)\n'
        '  list(APPEND uv_libraries bsd network)\n'
        '  list(APPEND uv_sources\n'
        '    src/unix/bsd-ifaddrs.c\n'
        '    src/unix/haiku.c\n'
        '    src/unix/no-fsevents.c\n'
        '    src/unix/no-proctitle.c\n'
        '    src/unix/posix-hrtime.c\n'
        '    src/unix/posix-poll.c)\n'
        'endif()\n\n'
    )
    anchor = 'if(CMAKE_SYSTEM_NAME STREQUAL "SunOS")'
    i = s.index(anchor)  # fail loudly if the upstream layout changed
    open(p, "w").write(s[:i] + block + s[i:])
    print("cmlibuv: inserted Haiku platform block")
else:
    print("cmlibuv: Haiku block already present")
PY

# ── 2. libarchive: use Haiku's system arc4random_buf (in libbsd) ────────────
F=Utilities/cmlibarchive/libarchive/archive_random.c
if ! head -1 "$F" | grep -q 'HAVE_ARC4RANDOM_BUF 1'; then
    printf '#define HAVE_ARC4RANDOM_BUF 1\n' > "$F.cvc"
    cat "$F" >> "$F.cvc"; mv "$F.cvc" "$F"
    echo "cmlibarchive: forced HAVE_ARC4RANDOM_BUF"
fi

# ── 3. bootstrap + build ────────────────────────────────────────────────────
# -lnetwork (sockets) + -lbsd (arc4random_buf) at the final link. --no-as-needed
# so the linker keeps them even though the reference is in a static sublib.
export LDFLAGS="${LDFLAGS:-} -Wl,--no-as-needed -lnetwork -lbsd"
# Haiku has no system curl/openssl dev by default; use cmake's bundled curl
# (no HTTPS via openssl needed for a build-tool cmake — recipes fetch sources
# through cvcpkg, not cmake's file(DOWNLOAD)).
./bootstrap \
    --prefix="${CVC_INSTALL_DIR}" \
    --parallel="${CVC_JOBS}" \
    -- \
    -DCMAKE_USE_OPENSSL=OFF
make -j"${CVC_JOBS}"
make install

echo "cmake (haiku): installed to ${CVC_INSTALL_DIR}"
"${CVC_INSTALL_DIR}/bin/cmake" --version | head -1
