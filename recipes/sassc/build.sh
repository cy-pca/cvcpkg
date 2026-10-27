#!/usr/bin/env bash
# recipes/sassc/build.sh — build SassC with LibSass linked in statically.
#
# The source dir is the sassc tarball. LibSass is downloaded here at a pinned
# sha256 and built by sassc's own Makefile (BUILD=static), so bin/sassc is a
# single binary and nothing of libsass is installed.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

SASSC_VER="3.6.2"
LIBSASS_VER="3.6.6"
LIBSASS_URL="https://github.com/sass/libsass/archive/refs/tags/${LIBSASS_VER}.tar.gz"
LIBSASS_SHA256="11f0bb3709a4f20285507419d7618f3877a425c0131ea8df40fe6196129df15d"

ARCHIVE="${CVC_BUILD_DIR}/libsass-${LIBSASS_VER}.tar.gz"
echo "Downloading ${LIBSASS_URL}..."
curl -fSL --retry 3 -o "${ARCHIVE}" "${LIBSASS_URL}"
ACTUAL="$(sha256sum < "${ARCHIVE}" | awk '{print $1}')"
if [ "${ACTUAL}" != "${LIBSASS_SHA256}" ]; then
    echo "sha256 mismatch for libsass ${LIBSASS_VER}: expected ${LIBSASS_SHA256}, got ${ACTUAL}" >&2
    exit 1
fi
LIBSASS="${CVC_BUILD_DIR}/libsass"
rm -rf "${LIBSASS}" && mkdir -p "${LIBSASS}"
tar xzf "${ARCHIVE}" -C "${LIBSASS}" --strip-components=1

# GitHub's tag archives carry no VERSION file and no .git, so both Makefiles
# would compile in an empty version string; `sassc --version` reports these.
export SASSC_VERSION="${SASSC_VER}" LIBSASS_VERSION="${LIBSASS_VER}"
make -C "${CVC_SOURCE_DIR}" -j "${CVC_JOBS}" \
    CC="${CC}" CXX="${CXX}" BUILD=static SASS_LIBSASS_PATH="${LIBSASS}"

mkdir -p "${CVC_INSTALL_DIR}/bin" "${CVC_INSTALL_DIR}/share/doc/sassc"
install -m 0755 "${CVC_SOURCE_DIR}/bin/sassc" "${CVC_INSTALL_DIR}/bin/sassc"
cp -p "${CVC_SOURCE_DIR}/LICENSE" "${CVC_INSTALL_DIR}/share/doc/sassc/LICENSE"
cp -p "${LIBSASS}/LICENSE" "${CVC_INSTALL_DIR}/share/doc/sassc/LICENSE.libsass"

"${CVC_INSTALL_DIR}/bin/sassc" --version
