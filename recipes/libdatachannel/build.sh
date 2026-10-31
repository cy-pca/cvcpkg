#!/usr/bin/env bash
# recipes/libdatachannel/build.sh -- build libdatachannel on linux and macOS.
#
# cvcpkg fetched and verified the tag's archive (recipe.yaml), in which every
# submodule is an empty directory.  Each one the library build reads is the
# GitHub archive of the commit v0.24.6's gitlink pins (`git ls-tree v0.24.6
# deps/`), checked against the sha256 here and unpacked into place.  Moving a
# pin is a reviewable edit to this table, and build.ps1 carries the same one.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck disable=SC1090
source "${SCRIPT_DIR}/../_common/env-${CVC_PLATFORM}.sh"

export PATH="${CVC_DEPS_PREFIX}/bin:${PATH}"

_sha256() {
    # Hash from stdin: a FILE operand with a backslash makes coreutils escape
    # its output.
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum < "$1" | awk '{print $1}'
    else
        shasum -a 256 < "$1" | awk '{print $1}'
    fi
}

# cvc_submodule <path> <owner/repo> <commit> <sha256>
cvc_submodule() {
    local path="$1" repo="$2" commit="$3" want="$4" archive got
    archive="${CVC_BUILD_DIR}/$(printf '%s' "${repo}" | tr '/' '_')-${commit}.tar.gz"
    mkdir -p "${CVC_BUILD_DIR}"
    curl -fsSL --retry 3 -o "${archive}" "https://github.com/${repo}/archive/${commit}.tar.gz"
    got="$(_sha256 "${archive}")"
    if [ "${got}" != "${want}" ]; then
        echo "libdatachannel: sha256 mismatch for ${repo}@${commit}: got ${got}, want ${want}" >&2
        exit 1
    fi
    rm -rf "${CVC_SOURCE_DIR:?}/${path}"
    mkdir -p "${CVC_SOURCE_DIR}/${path}"
    (cd "${CVC_SOURCE_DIR}/${path}" && tar -xzf - --strip-components=1) < "${archive}"
    if [ ! -f "${CVC_SOURCE_DIR}/${path}/CMakeLists.txt" ]; then
        echo "libdatachannel: ${repo}@${commit} has no CMakeLists.txt in ${path}" >&2
        exit 1
    fi
    echo "libdatachannel: ${path} <- ${repo}@${commit}"
}

cvc_submodule deps/plog \
    SergiusTheBest/plog 94899e0b926ac1b0f4750bfbd495167b4a6ae9ef \
    92a08bce559b5f28aa88d3fd9071567414b9f43a83fe2a05a6dd14f1da536072
cvc_submodule deps/usrsctp \
    paullouisageneau/usrsctp fec583d54493f879d2ae44a743423bf8a04371ab \
    e5c114afe73c9a0ec419fab5f5b3f63f3ce57b09b90d06ea85e63dca8aed3e7c
cvc_submodule deps/libjuice \
    paullouisageneau/libjuice b89c792e3612faf2f12cf35bcc56857313a06be3 \
    713b22174e8cb2e06538fa9bac2650bda21b83c86f5128813ab4dcf8f5bea9ed
cvc_submodule deps/libsrtp \
    cisco/libsrtp 24b3bf8f19b6f5ab4cd2bcceb4f4064efca86fd5 \
    063478e368d7cd13d04a908d152a46f90bfa728c4b324be6d413b0b92728207c

# Only cvcpkg's OpenSSL, never whatever FindOpenSSL would settle for on the host.
if [ ! -d "${CVC_DEPS_PREFIX}/include/openssl" ]; then
    echo "libdatachannel: no OpenSSL in CVC_DEPS_PREFIX (${CVC_DEPS_PREFIX}); install openssl first" >&2
    exit 1
fi

EXTRA=(
    -DNO_EXAMPLES=ON
    -DNO_TESTS=ON
    -DUSE_GNUTLS=OFF
    -DUSE_MBEDTLS=OFF
    -DUSE_NICE=OFF
    -DOPENSSL_ROOT_DIR="${CVC_DEPS_PREFIX}"
)

cvc_cmake_build "${EXTRA[@]}"
