#!/usr/bin/env bash
# recipes/mujoco-core-cp312/deps.sh (a copy of recipes/mujoco/deps.sh) — fill MuJoCo's dependency cache from deps.lock.
#
# Sourced by build.sh and build-wasm.sh.  Defines mj_fetch_deps, which reads
# deps.lock beside this file and, for every line:
#   * downloads the archive (curl, with retries),
#   * refuses it unless its sha256 matches the lock,
#   * stores it as <cache>/<library-name>-<git-commit>.tar.gz, the exact name
#     upstream's cmake/FindOrFetch.cmake looks for under MUJOCO_CMAKE_DEP_CACHE.
# MarchingCubeCpp is also extracted to <cache>/src/marchingcubecpp, because
# upstream fetches it with a bare FetchContent_Declare that only honours
# FETCHCONTENT_SOURCE_DIR_MARCHINGCUBECPP.
#
# After it returns, MJ_DEP_CACHE and MJ_MARCHINGCUBECPP_DIR are set; pass them
# as -DMUJOCO_CMAKE_DEP_CACHE / -DFETCHCONTENT_SOURCE_DIR_MARCHINGCUBECPP.
# mj_check_no_git_fetch fails the build when CMake fell back to a network clone
# anyway (a dependency added upstream and not yet in deps.lock), and
# mj_install_licenses copies the licence texts.  Plain bash 3.2.

_MJ_DEPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

_mj_sha256() {
    if command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{print $1}'
    elif command -v sha256 >/dev/null 2>&1; then
        sha256 -q "$1"
    else
        openssl dgst -sha256 "$1" | awk '{print $NF}'
    fi
}

mj_fetch_deps() {
    local lock="${1:-${_MJ_DEPS_DIR}/deps.lock}"
    MJ_DEP_CACHE="${2:-${CVC_BUILD_DIR}/mujoco-dep-cache}"
    mkdir -p "${MJ_DEP_CACHE}"
    local name commit sha url dest actual
    while read -r name commit sha url; do
        [[ -z "${name}" || "${name}" == \#* ]] && continue
        dest="${MJ_DEP_CACHE}/${name}-${commit}.tar.gz"
        if [[ ! -f "${dest}" ]] || [[ "$(_mj_sha256 "${dest}")" != "${sha}" ]]; then
            echo "mujoco: fetching ${name} @ ${commit}"
            curl -fsSL --retry 5 --retry-delay 3 -o "${dest}.part" "${url}"
            actual="$(_mj_sha256 "${dest}.part")"
            if [[ "${actual}" != "${sha}" ]]; then
                echo "mujoco: sha256 mismatch for ${url}" >&2
                echo "        expected ${sha}" >&2
                echo "        got      ${actual}" >&2
                rm -f "${dest}.part"
                return 1
            fi
            mv -f "${dest}.part" "${dest}"
        fi
        if [[ "${name}" == "marchingcubecpp" ]]; then
            MJ_MARCHINGCUBECPP_DIR="${MJ_DEP_CACHE}/src/marchingcubecpp"
            rm -rf "${MJ_MARCHINGCUBECPP_DIR}"
            mkdir -p "${MJ_MARCHINGCUBECPP_DIR}"
            tar -xzf "${dest}" -C "${MJ_MARCHINGCUBECPP_DIR}" --strip-components=1
        fi
    done < "${lock}"
    : "${MJ_MARCHINGCUBECPP_DIR:=}"
    export MJ_DEP_CACHE MJ_MARCHINGCUBECPP_DIR
}

# mj_install_licenses <dir>: MuJoCo's LICENSE plus the licence text of every
# dependency archive in the cache (all of them end up compiled into the
# shipped binaries).  MarchingCubeCpp has no licence file: its README declares
# it public domain, so that README is what is copied.
mj_install_licenses() {
    local dst="$1" lock="${2:-${_MJ_DEPS_DIR}/deps.lock}"
    mkdir -p "${dst}/third_party"
    cp -f "${CVC_SOURCE_DIR}/LICENSE" "${dst}/LICENSE"
    local name commit sha url tmp f n
    tmp="$(mktemp -d)"
    while read -r name commit sha url; do
        [[ -z "${name}" || "${name}" == \#* ]] && continue
        rm -rf "${tmp:?}"/*
        tar -xzf "${MJ_DEP_CACHE}/${name}-${commit}.tar.gz" -C "${tmp}" --strip-components=1
        n=0
        for f in "${tmp}"/LICENSE* "${tmp}"/LICENCE* "${tmp}"/COPYING* "${tmp}"/*-LICENSE; do
            [[ -f "${f}" ]] || continue
            cp -f "${f}" "${dst}/third_party/${name}-$(basename "${f}")"
            n=$((n + 1))
        done
        if [[ ${n} -eq 0 && "${name}" == "marchingcubecpp" ]]; then
            cp -f "${tmp}/README.md" "${dst}/third_party/${name}-README.md"
            n=1
        fi
        [[ ${n} -gt 0 ]] || { echo "mujoco: no licence text found for ${name}" >&2; rm -rf "${tmp}"; return 1; }
    done < "${lock}"
    rm -rf "${tmp}"
}

# Upstream prints "Using FetchContent for <lib>: <git url>" when no cached
# tarball was found and it is about to clone; "Using package cache" otherwise.
mj_check_no_git_fetch() {
    local log="$1"
    if grep -q "mujoco::FindOrFetch: Using FetchContent for" "${log}"; then
        echo "mujoco: CMake cloned a dependency that deps.lock does not pin:" >&2
        grep "mujoco::FindOrFetch: Using FetchContent for" "${log}" >&2
        return 1
    fi
}
