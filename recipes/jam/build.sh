#!/usr/bin/env bash
# recipes/jam/build.sh — build Haiku's Jam from the buildtools tree.
#
# Haiku maintains its own Jam (a fork of Perforce Jam) in the `jam/`
# subdirectory of its buildtools repo.  There is no release tarball, so we
# shallow-clone the repo at a pinned branch and run its bootstrap Makefile.
set -euo pipefail

: "${CVC_INSTALL_DIR:?CVC_INSTALL_DIR must be set}"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

BUILDTOOLS_REF="${BUILDTOOLS_REF:-r1beta5}"
BUILDTOOLS_REPO="${BUILDTOOLS_REPO:-https://github.com/haiku/buildtools.git}"

SRC="${CVC_BUILD_DIR}/buildtools"
if [[ ! -d "${SRC}" ]]; then
    git clone --depth 1 --branch "${BUILDTOOLS_REF}" "${BUILDTOOLS_REPO}" "${SRC}"
fi

cd "${SRC}/jam"

# Jam bootstraps itself: the Makefile first builds a `jam0` with the host cc,
# then rebuilds `jam` with jam0.  Some trees ship a make.sh instead.
if [[ -f Makefile ]]; then
    make CC="${CC:-cc}" CFLAGS="${CFLAGS:--O2}"
elif [[ -f build.sh ]]; then
    CC="${CC:-cc}" ./build.sh
else
    echo "no Makefile/build.sh in jam/ — layout changed?" >&2
    exit 1
fi

# Locate the freshly built jam binary (Haiku drops it in bin.<arch>/ or the
# jam/ dir itself, named `jam`).
JAM_BIN="$(find . -maxdepth 2 -type f -name jam -perm -u+x 2>/dev/null | head -1)"
if [[ -z "${JAM_BIN}" ]]; then
    # Fall back to the bootstrap binary name if the second stage didn't run.
    JAM_BIN="$(find . -maxdepth 2 -type f -name 'jam0' -perm -u+x 2>/dev/null | head -1)"
fi
[[ -n "${JAM_BIN}" ]] || { echo "jam binary not found after build" >&2; exit 1; }

mkdir -p "${CVC_INSTALL_DIR}/bin"
cp "${JAM_BIN}" "${CVC_INSTALL_DIR}/bin/jam"
chmod +x "${CVC_INSTALL_DIR}/bin/jam"

echo "jam (${BUILDTOOLS_REF}) built and staged to ${CVC_INSTALL_DIR}/bin/jam"
"${CVC_INSTALL_DIR}/bin/jam" -v 2>/dev/null || true
