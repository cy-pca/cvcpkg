#!/usr/bin/env bash
# recipes/drjit-src/build.sh -- stage Dr.Jit 1.5.0's source tree, with every
# git submodule its build reads, at src/drjit-src/ (a source package: see
# docs/source-recipes.md).
#
# cvcpkg fetched and verified the superproject's commit archive (recipe.yaml),
# in which each submodule is an empty directory.  Each submodule below is the
# GitHub archive of the commit its parent's gitlink pins (read with
# `git ls-tree <parent commit> <path>`), checked against the sha256 recorded
# here and unpacked into place, parents before children.  Moving any pin is a
# reviewable edit to this table.
set -euo pipefail
# shellcheck disable=SC1091
. "$(dirname "$0")/../_common/stage-source.sh"
: "${CVC_BUILD_DIR:?CVC_BUILD_DIR must be set}"

cvc_stage_source
dest="${CVC_INSTALL_DIR}/src/${CVC_COMPONENT}"
manifest="${dest}/cvcpkg-submodules.txt"

_sha256() {
    # Hash from stdin: a FILE operand with a backslash makes coreutils escape
    # its output (a Windows builder's paths).
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
    curl -fSL --retry 3 -o "${archive}" "https://github.com/${repo}/archive/${commit}.tar.gz"
    got="$(_sha256 "${archive}")"
    if [ "${got}" != "${want}" ]; then
        echo "drjit-src: sha256 mismatch for ${repo}@${commit}: got ${got}, want ${want}" >&2
        exit 1
    fi
    rm -rf "${dest:?}/${path}"
    mkdir -p "${dest}/${path}"
    # Unpack inside the directory, reading the archive from stdin: on a Windows
    # builder GNU tar takes "C:\..." in -f as host:path and unquotes escapes in
    # -C names, so tar never sees a Windows path.
    (cd "${dest}/${path}" && tar -xzf - --strip-components=1) < "${archive}"
    if [ -z "$(ls -A "${dest}/${path}")" ]; then
        echo "drjit-src: ${repo}@${commit} unpacked nothing into ${path}" >&2
        exit 1
    fi
    printf '%s %s %s\n' "${path}" "${repo}" "${commit}" >> "${manifest}"
    echo "drjit-src: ${path} <- ${repo}@${commit}"
}

printf '# path repo commit -- the submodules of mitsuba-renderer/drjit@671cbb25244be8a9731ac7b0e40e60ff122b8f8d\n' > "${manifest}"

cvc_submodule ext/drjit-core \
    mitsuba-renderer/drjit-core 3b96411288b9938889045308654ba37d4ef24b71 \
    095f422a592172535a7fc834e5e778ce219027a507f25d1295f884679c6d5c8e
cvc_submodule ext/drjit-core/ext/nanothread \
    mitsuba-renderer/nanothread a2097209e10852962c485ee46d84d28e7a63a1a1 \
    ed4abfaec8a12c796f238525b501871ea2c5e250a508fbb572c3fa75644600b2
cvc_submodule ext/drjit-core/ext/nanothread/ext/cmake-defaults \
    mitsuba-renderer/cmake-defaults d65442956cd78772ba26ba25dc552de22566b717 \
    fad0f95617687bfc30a899261e4d00b12f43ae8cd81e775f3f75a2961d4f8ec5
cvc_submodule ext/drjit-core/ext/robin_map \
    mitsuba-renderer/robin-map f70fd39a960385ef0a963aef00b750e4740228c0 \
    d8575d4d580f5378f01b0b51bc5c2738005b94fff20db732852475972b6645f7
# nanobind v2.14.0.  Under scikit-build drjit compiles against the nanobind
# Python package (nanobind-cp313), not this copy; its robin_map headers are
# still on drjit-python's include path (src/python/CMakeLists.txt).
cvc_submodule ext/nanobind \
    wjakob/nanobind 86e5626728fc282637a6c338597120913dded1bb \
    fee2d84337ad3a14609091b8693399028eb957b8a83493ce2825db2b36a52d8c
cvc_submodule ext/nanobind/ext/robin_map \
    Tessil/robin-map 4ec1bf19c6a96125ea22062f38c2cf5b958e448e \
    4968947bd2e908854481519ec422823af4161a286ff1e16943293b753e80cb3f

[ -f "${dest}/ext/drjit-core/ext/nanothread/ext/cmake-defaults/CMakeLists.txt" ] \
    || { echo "drjit-src: cmake-defaults missing after unpacking" >&2; exit 1; }
echo "drjit-src: staged $(grep -vc '^#' "${manifest}") submodules"
